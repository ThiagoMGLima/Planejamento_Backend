# Fase 0B — PR0 e PR1: views finas, `Perfil`, `dono` e isolamento

> Passo 2–3 do ciclo do `ROADMAP.md`. **Este documento é para revisão antes de
> qualquer código.** As dúvidas da seção 5 precisam de resposta para a
> implementação começar.
>
> **Histórico:** a v1 (24/07/2026) propunha um PR1 único que remendava os 32 pontos
> de query. Revisada no mesmo dia: os achados são **um default errado**, não quatro
> bugs — daí o default invertido (seção 3) e o PR0 destacado.

## 1. Escopo

> **Precedido pelo PR0** (`0B.9`): views finas + ferramentas do agente em processo.
> Ver seção 3.3 — é pré-requisito estrutural, não faz parte deste PR.

**Entra:** model `Perfil`; FK `dono` nos 8 models-raiz; **manager que exige escopo**
(0B.10); `dono` como parâmetro obrigatório dos services; unicidade por-dono;
serializers gravando `dono` do request; seed das classes padrão por-usuário; `dono`
nos jobs assíncronos; migração dos dados existentes.

**Não entra:** Supabase, JWT, login (PR2); gate de pagamento (PR3). Enquanto não há
auth, um **perfil local default** resolve o `request.user` — a API continua aberta em
`localhost` e o comportamento externo não muda.

O critério de pronto do PR1 é: *o schema e todo o caminho de dados já são
multi-tenant; falta só trocar quem diz "quem é o usuário".*

---

## 2. Análise do código atual

### 2.1 Onde o `dono` precisa existir

8 models-raiz (`planner/models.py`): `Classe`, `Tarefa`, `Evento`,
`RegraRecorrencia`, `PesoPreferencia`, `EscolhaCenario`, `RegistroExecucao`,
`FeriadoLocal`.

Só `Ocorrencia` herda pelo pai (`evento` é `CASCADE` e não-nulo). Vale notar por que
os outros **não** podem herdar:

- `RegraRecorrencia` é *apontada* por `Evento`, não aponta — não há caminho até o dono.
- `RegistroExecucao` tem `tarefa`/`evento`/`classe` os três **nulos e `SET_NULL`**
  (`models.py:185-205`): apagada a tarefa e o evento, o registro fica órfão e sem
  nenhum caminho para o dono. Precisa de `dono` próprio.
- `EscolhaCenario` não tem FK nenhuma — só `JSONField` (`models.py:163-166`).

### 2.2 Unicidade que hoje é global

| Onde | Constraint | Efeito multi-tenant |
| --- | --- | --- |
| `models.py:30` | `Classe.nome unique=True` | 2º usuário não consegue ter "Estudar" |
| `models.py:145` | `PesoPreferencia.metrica unique=True` | **o 1º a gravar um peso trava o aprendizado de todos** |
| `models.py:273` | `uq_feriadolocal_data (dia, mes, ano)` | um feriado municipal por data no sistema inteiro |
| `models.py:242` | `uq_ocorrencia_evento_data` | ✅ já seguro — `evento` já é por-dono |

### 2.3 Superfície de isolamento — 32 pontos de query

15 em views/serializers e 17 nos services (fora de testes, migrations e seeds).

Views e serializers (o fácil, resolve com mixin + queryset escopado):

- `views.py:62,80,170` — os três `queryset` de ViewSet.
- `views.py:108,148` — `Evento.objects.create` em `promover`/`planejar`.
- `views.py:322` — `/pendentes`.
- `views.py:613` — `EscolhaCenario.objects.create`.
- `serializers.py:41,94,184,216` — **4× `queryset=Classe.objects.all()`** em
  `PrimaryKeyRelatedField`. Buraco real: sem escopo, um usuário anexa a **classe de
  outro** ao próprio evento. É o vazamento mais fácil de deixar passar.

Services (o difícil — não têm `request`):

| Arquivo | Função | O que consulta global |
| --- | --- | --- |
| `planejamento.py:208,221` | `intervalos_ocupados` | `Evento` (o "ocupado" do solver) |
| `planejamento.py:492` | `validar_tarefas` | `Tarefa` |
| `replanejamento.py:37,78,237` | `_sessoes_futuras`, `_pool_e_substituiveis` | `Evento`, `Tarefa` — **inclui um `.delete()`** |
| `aplicacao.py:41,58` | `aplicar_sessoes` | `Tarefa`, cria `Evento` |
| `adaptacao.py:55,71,99` | `pesos_atuais`, `decair_pesos`, `atualizar_pesos` | `PesoPreferencia` |
| `adaptacao.py:124,155` | `fator_classe`, `flexibilidade_classe` | `RegistroExecucao` |
| `holidays.py:86` | `_municipais` | `FeriadoLocal` |
| `completion.py:33,46,57` | `concluir`/`remarcar` | cria `Ocorrencia`, `Tarefa`, `RegistroExecucao` |

**`replanejamento.py:237` é o mais perigoso:** `Evento.objects.filter(id__in=removidos).delete()`.
Sem escopo, um `id` forjado apaga evento alheio.

### 2.4 `holidays.feriados_do_ano` é o ponto de merge — e muda de assinatura

O próprio docstring diz que é "o PONTO ÚNICO de merge" que alimenta recorrência,
solver e `GET /feriados`. Hoje é `feriados_do_ano(ano)`. As camadas se separam bem:

- **Nacional** (BrasilAPI) e **estadual** (lib `holidays`) são **fatos globais** —
  cache por ano continua compartilhado entre usuários, sem vazamento.
- **Municipal** (`FeriadoLocal`, do DB) vira **por-dono**.

Logo: `feriados_do_ano(ano, dono)`, com o cache global preservado só nas duas
primeiras camadas. Bom negócio — a chamada externa cara continua amortizada entre
todos os usuários.

### 2.5 Cache — o que já está seguro e o que não está

Já seguro por acidente feliz (chaves derivadas de UUID):

- `tasks.py:31` `_chave_cache` — deriva de `tarefa_ids` (UUIDs) + prefs + plano base.
- `adaptacao.py:116` `fator_classe:{classe_id}` — UUID.

Ainda assim vale **incluir o `dono` nas chaves**: a segurança hoje depende de os
UUIDs serem imprevisíveis, não de um limite explícito. É defesa em profundidade barata.

**Não seguro:** os endpoints de status de job. `views.py:567,591,659,711,754` fazem
`cache.get(f"cenarios_job:{job_id}")` / `AsyncResult(job_id)` e **devolvem o resultado
sem verificar posse** (9 usos de `AsyncResult`). Com contas, quem tiver um `job_id`
lê o plano alheio — o que inclui títulos de tarefas e a agenda inteira. Precisa
gravar o `dono` junto do job e conferir no GET.

### 2.6 Assíncrono e identidade fora do request (0B.9)

`tasks.py` tem 4 jobs (`planejar_ia_task`, `gerar_cenarios_task`,
`refinar_cenario_task`, `agente_chat_task`). Nenhum recebe identidade — hoje não
precisa. Todos chamam services que consultam o DB global.

Pior: `agente.py:50` monta `f"{settings.API_BASE_URL}{caminho}"` e chama a **própria
API por HTTP, de dentro do worker**, sem header de autenticação. O mesmo em
`mcp_server/server.py:38`. Quando a auth entrar, ambos tomam 401.

### 2.7 Permissões

`config/settings.py`: `DEFAULT_PERMISSION_CLASSES = [AllowAny]`, e **18
`@permission_classes([AllowAny])`** espalhados em `views.py`. No PR1 isso fica como
está de propósito (não há login ainda); o PR2 inverte o default e remove os
decorators, menos em `/health`.

### 2.8 Seeds, migrations e testes

- `0002_seed_classes.py` cria as 5 classes **no migrate**, globais. Vira seed
  por-perfil (0B.6) — o migrate não sabe mais para quem semear.
- `0006_seed_feriados_curitiba.py` — mesmo problema (ver dúvida Q5).
- `seed_demo.py` / `seed_planejamento.py` — `Classe.objects.all()` e vários
  `.objects.all().delete()`; precisam de `--dono` (ou assumir o perfil local).
- `tests/factories.py` — 5 factories sem `dono`; é o ponto de alavanca para as
  ~3.800 linhas de teste. Um `SubFactory(PerfilFactory)` com `django_get_or_create`
  mantém a maioria dos testes existentes compilando sem edição.

---

## 3. Desenho: inverter o default, em vez de remendar 32 pontos

**Revisado em 24/07/2026.** A leitura inicial tratava os achados da seção 2 como
quatro problemas independentes. Não são: são **um default errado**. Toda consulta
nasce global, e o isolamento depende de alguém lembrar de escopar 32 vezes seguidas,
para sempre, inclusive em código que ainda não existe. Pelo princípio 9 do
`ROADMAP.md`, isso está no nível mais fraco da escala ("convenção documentada").

Três decisões, que juntas sobem o invariante para "falha no teste":

### 3.1 Acesso global vira explícito (0B.10)

O manager default dos 8 models-raiz **exige escopo**: `Evento.objects.all()` levanta.
Quem precisa do global escreve `Evento.objects.sem_escopo()` — **grepável e visível na
revisão**. Usam isso os seeds e o admin; mais ninguém.

Isso resolve os 32 pontos de uma vez e, mais importante, **protege o código futuro**:
o próximo service que alguém escrever não tem como esquecer.

### 3.2 Identidade é parâmetro do domínio, nunca ambiente

Os ~17 pontos de service recebem `dono` como argumento **obrigatório**. A alternativa
(`contextvar` setada por middleware) foi **descartada**:

```python
def intervalos_ocupados(agora, fim, excluir=None):
    dono = _dono_atual.get(None)          # no worker Celery: None
    qs = Evento.objects.filter(inicio__lt=fim, fim__gt=agora)
    if dono:                              # ← este `if` é a falha
        qs = qs.filter(dono=dono)
```

No worker o `dono` é `None`, o filtro não acontece, e o solver trata a agenda de
**todos os usuários** como "ocupado" — plano errado, dados vazados, **sem exceção e
sem log**. E a suíte **não pega**: ela roda com um perfil só, onde "global" e "do
dono" são o mesmo conjunto. Os 3.800 linhas passariam verdes com o vazamento dentro.

Fazer o `contextvar` levantar em vez de usar `if dono` funciona, mas exige lembrar
disso nos 17 pontos — é o mesmo problema de memória, só mudou de lugar.

### 3.3 O agente deixa de ser cliente HTTP de si mesmo (0B.9 → PR0)

`agente.py:47` é código Django fazendo `requests` para o próprio Django. As
ferramentas passam a chamar os **services em processo**; o `dono_id` já vem no payload
da task. Sem token na fila, sem 401, sem expiração — e a questão da credencial
**deixa de existir** em vez de ser resolvida.

O MCP server é outra coisa: container separado, clientes externos, fronteira legítima
— segue HTTP e ganha credencial de serviço própria.

> **Sobre renovar o token** (a saída "óbvia" para a expiração): funciona, mas para
> renovar o worker precisa do *refresh token*, que emite access tokens
> indefinidamente. Guardá-lo na fila do Celery — Redis **sem senha e com persistência
> em disco** neste compose — é pior que guardar o access token. A renovação resolve a
> expiração escalando a credencial.

### 3.4 Jobs assíncronos carregam o dono

`dono` no payload da task, na chave de cache e no resultado; os 5 endpoints de status
conferem posse. Hoje a proteção é o `job_id` ser um UUID difícil de adivinhar — o que
é obscuridade, não fronteira (princípio 9).

---

## 4. Passos propostos

### PR0 — views finas + agente em processo

1. Mover a regra de `promover` e `planejar` de `views.py:107-166` para
   `services/` (criação do `Evento` + transição da `Tarefa`).
2. Trocar as ferramentas de `agente.py` (`_api` → chamadas de service). Os
   contratos das ferramentas não mudam — muda o transporte.
3. Testes: os de API continuam valendo como gabarito (comportamento externo
   idêntico); novos testes de service para a lógica que desceu.

### PR1 — `Perfil` + `dono` + default invertido

1. **`Perfil`** (`models.py`) — PK UUID, `email`, `nome`, `plano`, `trial_ate`,
   `criado_em`. Sem FK para `auth.User` (o PR2 traz o Supabase).
2. **Manager que exige escopo** (0B.10) nos 8 raiz, com `sem_escopo()` explícito.
   **Vem antes do resto**: com ele no lugar, a suíte aponta sozinha cada um dos 32
   pontos que precisa de ajuste, em vez de eu procurar a olho.
3. **`dono = FK(Perfil, on_delete=CASCADE)`** nos 8 raiz + migration.
   `null=True` **temporário** para permitir o backfill, apertado para `NOT NULL` na
   migration seguinte, no mesmo PR.
4. **Data migration** — cria o "perfil local", atribui todas as linhas existentes a
   ele (ver Q3/Q4).
5. **Unicidade por-dono** — `Classe` (`dono`,`nome`), `PesoPreferencia`
   (`dono`,`metrica`), `FeriadoLocal` (`dono`,`dia`,`mes`,`ano`).
6. **Views** — mixin de queryset + `perform_create` gravando `dono`.
7. **Serializers** — escopar os 4 `queryset=Classe.objects.all()`; `dono` nunca
   vem do cliente (`read_only`).
8. **Services** — `dono` **obrigatório** nos 17 pontos da tabela 2.3;
   `feriados_do_ano(ano, dono)` mantendo o cache global das camadas nacional/estadual.
9. **Celery** — `dono_id` no payload das 4 tasks; `dono` na chave de cache e no
   resultado; verificação de posse nos 5 endpoints de status.
10. **MCP** — credencial de serviço (única fronteira HTTP que sobra).
11. **Seeds** — `seed_classes_padrao(perfil)` na criação do `Perfil`; `--dono` nos
    dois comandos; `sem_escopo()` onde eles varrem tudo.
12. **Testes** — `PerfilFactory`; `dono` nas 5 factories; **testes novos de
    isolamento** (ver seção 6).

---

## 5. Dúvidas em aberto

> ✅ **Q1 (threading do `dono`)** e **Q2 (identidade do agente/MCP)** foram
> **respondidas em 24/07/2026** e viraram as seções 3.2 e 3.3. Restam Q3–Q6.

**Q3. Identidade do `Perfil` no PR1.** O PR2 quer PK = UUID do Supabase. No PR1 não
há Supabase. Gero um UUID local e no PR2 reconcilio (migration que troca a PK, ou uma
coluna `supabase_id` separada)? **Sugiro `supabase_id` nulo separado da PK** — troca
de PK com 8 FKs apontando é migration cara e arriscada.

**Q4. Dados existentes.** Sua base de dev tem o `seed_demo` (23 tarefas, 28 eventos).
Backfill para o perfil local resolve o PR1. Quando o PR2 chegar e você logar com
Google, esse perfil local vira o seu (associando o `supabase_id` ao perfil existente)
ou vira uma conta nova e os dados de dev ficam órfãos? A 1ª é mais amigável; a 2ª é
mais limpa e testa melhor o fluxo de conta nova.

**Q5. `FeriadoLocal` por-dono vs catálogo global.** A migration `0006` semeia o
feriado de Curitiba globalmente. Feriado municipal é *fato do município*, não do
usuário — replicá-lo por conta é redundante. Duas leituras: (i) por-dono como manda
o 0B.5, cada usuário mantém a lista dele; (ii) global com um campo de município,
compartilhado. **Sugiro (i) agora** (mais simples, e o 0B.5 já decidiu), deixando
(ii) para quando houver seleção de município — mas quero seu aval porque é o único
item onde o ROADMAP e a natureza do dado divergem.

**Q6. Admin.** Filtra por dono, ou continua visão global de superuser? **Sugiro
global** — é ferramenta de operador e você é o único a usar.

---

## 6. Como o PR será testado

> **A suíte atual não é gabarito de isolamento.** Ela roda com um perfil só, onde
> "global" e "do dono" coincidem — passaria verde com vazamento. Os testes abaixo
> **precisam de dois perfis**; sem isso, nada aqui prova nada.

- Suíte atual verde (gabarito de que o backfill e as factories não quebraram nada).
- **Testes de isolamento novos**: dois perfis, e para cada recurso — listar, ler por
  id, atualizar, apagar, e as ações (`promover`, `planejar`, `concluir`, `remarcar`).
- **Cross-tenant por FK**: usuário A tentando criar evento com `classe_id` de B → 400.
- **Jobs**: A dispara plano, B tenta ler o `job_id` → 404.
- **Replanejar**: B não consegue apagar sessões de A.
- Solver, feriados e pesos: A e B com dados diferentes produzem planos diferentes.
- `ruff`, `black --check`, `makemigrations --check --dry-run`.
