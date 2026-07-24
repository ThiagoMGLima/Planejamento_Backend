# Contexto — Fase 0B / PR1: `Perfil`, `dono` e default invertido

> Passo 7 do ciclo do `ROADMAP.md`: o que era para fazer, o que foi feito, as
> decisões, os bugs e o estado em que a próxima task começa.
> Plano: [`fase0b-pr1-dono.md`](fase0b-pr1-dono.md). Task anterior:
> [`contexto-0b-pr0.md`](contexto-0b-pr0.md). **Concluída em 24/07/2026.**

## 1. O que era para fazer

Tornar o backend inteiro multi-tenant **antes** de existir login, para que o PR2
precise trocar só uma função. Critério de pronto do plano:

> *o schema e todo o caminho de dados já são multi-tenant; falta só trocar quem
> diz "quem é o usuário".*

Isso foi atingido. `services/perfis.perfil_do_request()` é a única função que o
PR2 precisa reescrever.

## 2. As quatro dúvidas, e o que o usuário decidiu

| | Decisão | Por quê |
| --- | --- | --- |
| **Q3** identidade do `Perfil` | PK = UUID local; `supabase_id` em coluna separada | 8 FKs apontam para a PK: trocá-la no PR2 seria reescrever 8 tabelas com dados dentro; preencher uma coluna é um `UPDATE` |
| **Q4** dados de dev | O perfil local **vira** a conta do usuário no PR2 | Ele também é cliente do produto; o 1º login grava o `supabase_id` no perfil existente e o `seed_demo` segue no lugar |
| **Q5** `FeriadoLocal` | Por-dono, como manda o 0B.5 | A redundância é aceitável enquanto não houver seleção de município |
| **Q6** admin | Global (`sem_escopo()`) + `list_filter = ["dono"]` | Ver seção 4 — a pergunta foi respondida, mas não com nenhuma das duas opções oferecidas |

**Verificação que mudou a Q3:** o usuário achava que o Supabase já estava
implementado. Não está — aparece só em documentação (`ROADMAP.md`, `CLAUDE.md`,
os dois planos), zero código: sem `Perfil`, sem `request.user`, sem dependência
de JWT, e as 6 migrations sem nada de auth. Por isso o PR1 resolveu identidade
sozinho.

## 3. O que foi feito

### 3.1 O manager que exige escopo (`planner/managers.py`) — a peça central

`Evento.objects.all()` levanta `EscopoAusente`. Quem consulta diz de quem:
`do_dono(perfil)` ou `sem_escopo()` (grepável, só seeds e admin).

A guarda mora no **QuerySet**, não no Manager: é na avaliação que a consulta vira
dados. `_fetch_all` cobre iteração/`get`/`first`/`values_list`; `count`, `exists`,
`aggregate`, `iterator`, `in_bulk`, `update` e `delete` são interceptados um a um
porque não passam por ele. `_clone` propaga a marca, senão o primeiro `.filter()`
depois do `do_dono()` a perderia.

`create()` fica livre de propósito: quem barra escrita sem dono é o `NOT NULL`.

**Isso valeu o PR inteiro:** ligado o manager, a suíte apontou sozinha 86 falhas,
cada uma num ponto real que precisava de escopo. Não precisei procurar os 32
pontos da análise a olho — e os que a análise não tinha achado apareceram junto.

### 3.2 Models e migrations

`Perfil` + FK `dono` obrigatória nos 8 raiz. `Ocorrencia` fica sem: herda pelo
`evento` (CASCADE, não-nulo). Unicidade global → por-dono em `Classe.nome`,
`PesoPreferencia.metrica` e `FeriadoLocal`; o índice de janela do `Evento` passou
a começar pelo `dono`.

Três migrations que formam **uma unidade** — rodar só a primeira deixa o schema
num estado que o código não suporta:

1. `0007_perfil_e_dono_nulo` — cria `Perfil`, adiciona `dono` **nulo** (única
   forma de adicionar coluna a tabela com dados);
2. `0008_backfill_perfil_local` — cria o perfil local (PK sentinel fixa) e adota
   tudo que existe, incluindo as 5 classes da `0002` e o feriado da `0006`;
3. `0009_dono_obrigatorio` — `NOT NULL` + as constraints por-dono.

A `0002` e a `0006` **não foram tocadas**: são história. Em banco novo elas rodam
antes e a `0008` adota o que criaram — o resultado é o mesmo.

### 3.3 Identidade é parâmetro, nunca ambiente

Os services recebem `dono` como argumento obrigatório. O `contextvar` foi
descartado no plano e a implementação confirma o motivo: no worker Celery ele
seria `None`, o filtro sumiria sem exceção e sem log, e a suíte — um perfil só —
passaria verde.

Onde o dono é **derivado**, não recebido, ninguém pode errar: `promover`/
`planejar` tiram do `tarefa.dono`, `completion` do `evento.dono`,
`atualizar_pesos` do `escolha.dono`. `ResultadoPlano` carrega o `dono` para o
contexto da IA e os cenários não precisarem recebê-lo por fora.

### 3.4 Fronteiras que não eram óbvias

- **FK cruzada** — `ClasseDoDonoField` escopa os 4 `PrimaryKeyRelatedField` por
  requisição. Era o vazamento mais fácil de deixar passar: sem escopo o DRF
  responde 201, porque do ponto de vista dele o id existe.
- **Jobs** — `job_id` deixou de ser credencial. Registro de posse em
  `job_dono:{id}` e 404 nos 5 endpoints de status + `escolher` e `refinar`.
- **Agente** — as ferramentas recebem `dono` como primeiro **posicional**, e o
  dispatch o passa por fora do `**tc.args`. Estrutural: nenhuma saída do LLM
  chega perto de decidir de quem são os dados.
- **Cache** — `dono` nas chaves de `_chave_cache`, `fator_classe` e da memória do
  agente (`conversa_id` vem do front; dois perfis podem mandar o mesmo).
- **`replanejamento.py:237`**, o `.delete()` que o plano marcou como o ponto mais
  perigoso: escopado.

### 3.5 Seeds e testes

`seed_classes_padrao(perfil)` desceu da migration `0002` para `services/perfis.py`
(0B.6): no migrate não havia para quem semear. Os dois comandos ganharam `--dono`
(default: perfil local), e o `--clear` só apaga do perfil semeado.

`PerfilFactory` **é o perfil local** — o mesmo que a API resolve. Sem isso, todo
teste de API criaria dados num perfil e leria de outro, e falharia por um motivo
sem relação com o que testa.

## 4. Decisões de desenho que fugiram do plano

**Admin (Q6).** O usuário pediu "ver um perfil por vez". A leitura literal exigiria
inventar no Django admin a noção de "perfil atual" que ele não tem: seleção em
sessão, seletor no topo, cada `ModelAdmin` escopado nisso — máquina nova só para o
painel. Adotado `list_filter = ["dono"]` + coluna `dono`: escolhe-se o perfil na
barra lateral e trabalha-se numa conta por vez, ao custo de três linhas por
`ModelAdmin`. Investigar o bug de um testador vira trocar o filtro, não trocar de
conta. **As duas opções que eu ofereci na pergunta eram um par falso.**

**Related managers reversos herdam a guarda.** `tarefa.eventos.all()` levanta —
Django deriva o related manager do `_default_manager`. Não foi contornado: a
mensagem diz o que fazer, e explicitar o escopo é o ponto do PR. Um teste
(`test_api.py`) foi ajustado. Forward FK (`evento.classe`) e `prefetch_related`
de `Ocorrencia` seguem intactos, porque usam o `_base_manager`.

## 5. Bugs encontrados

**`KeyError: 'request'` em `promover`/`planejar`.** `ClasseDoDonoField` lê o perfil
de `self.context["request"]`, mas as views instanciavam `PromoverSerializer(data=...)`
**sem contexto**. Qualquer chamada com `classe_id` explodiria em 500. Corrigido
passando `context=self.get_serializer_context()`.

Vale registrar como foi achado: **nenhum teste existente pegou** — os de API que
exercitam `promover` não passam `classe_id`, e a suíte inteira estava verde. Quem
pegou foi `test_promover_com_classe_do_outro_da_400`, escrito para provar
isolamento. É exatamente o argumento da seção 6 do plano: a suíte antiga não é
gabarito para esta classe de problema.

Dois defeitos meus nos próprios testes de isolamento, corrigidos: um `EventoFactory`
sem `status` explícito (o factory não tem default) e um assert de igualdade exata
em feriados que ignorava o feriado de Curitiba herdado no backfill.

## 6. Como foi verificado

- **270 testes verdes**, incluindo **39 novos de isolamento** (`test_isolamento.py`).
- `ruff`, `black --check`, `makemigrations --check --dry-run` limpos.
- **Migrations aplicadas no banco de dev**: 23 tarefas, 28 eventos, 8 regras, 15
  registros, 5 classes e 1 feriado — **zero órfãos**, todos no perfil local.
- Smoke test na API viva: `/health`, `/classes/`, `/pendentes`, `/feriados`,
  `/eventos/` e `/planejamento/calcular` (16 sessões) respondendo como antes;
  job inventado → 404.

Sobre os testes de isolamento: eles são os **únicos** que provam alguma coisa
aqui. O resto da suíte roda com um perfil só, onde "todos os dados" e "os dados
do dono" são o mesmo conjunto — passaria verde com um vazamento dentro.

## 7. O que NÃO entrou, e por quê

**Credencial de serviço do MCP** (passo 10 do plano) — **movida para o PR2**. Não
há o que autenticar enquanto a API não tem autenticação nenhuma; a credencial só
faz sentido junto com o `SupabaseJWTAuthentication`. O MCP segue funcionando, sem
auth, operando no perfil local (é quem a API resolve).

`AllowAny` e os 18 `@permission_classes` continuam como estavam, de propósito: o
PR2 inverte o default e os remove, menos em `/health`.

## 8. Onde a próxima task começa

**PR2 — Supabase Auth (0B.1/0B.2). Bloqueado no projeto Supabase criado — isso é
do usuário, não do agente.**

O PR1 deixou o PR2 estreito. O que ele precisa fazer:

1. `SupabaseJWTAuthentication` (DRF), validando o JWT por segredo/JWKS.
2. **Reescrever `services/perfis.perfil_do_request()`** para devolver o perfil do
   token em vez do local. É a única mudança de comportamento no backend inteiro.
3. Provisionamento JIT no 1º acesso: `Perfil` novo + `seed_classes_padrao()`. No
   primeiro login do **usuário**, gravar o `supabase_id` no **perfil local
   existente** em vez de criar conta nova (decisão Q4) — é o que preserva o
   `seed_demo` de dev.
4. Inverter `DEFAULT_PERMISSION_CLASSES` e remover os 18 `@permission_classes`
   (exceto `/health`).
5. Credencial de serviço do MCP (herdada deste PR, ver seção 7).

Os 39 testes de isolamento continuam valendo palavra por palavra depois do PR2 —
muda só quem responde "quem é você". Se algum deles quebrar lá, é regressão real.
