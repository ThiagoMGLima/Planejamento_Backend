# ROADMAP — Planejador de Rotina (de projeto pessoal a produto)

> Esqueleto vivo. Consolida as decisões do brainstorm de produto e marca os pontos
> em aberto. **Não é contrato de implementação** (isso segue nos `PLAN.md` / handoffs
> por marco) — é o mapa macro.

## Princípios que guiam tudo

1. **Beta técnico primeiro, não-hospedado, JÁ com contas.** Distribui pros **amigos
   técnicos** via Docker (cada um roda a própria cópia); eles testam também o
   **sistema de contas e a segurança do Supabase Auth**. Leigos e hospedagem ficam
   pra depois.
2. **Fundação de contas entra no beta; regras de negócio sobem por cima.** Como os
   testadores vão exercitar auth/isolamento, o `dono` vem **agora** — antes de as regras
   assentarem. Aceita-se o **retrabalho leve** de re-tocar os models quando as regras
   mudarem (Fase 1).
3. **Não-hospedado + Supabase Auth convivem.** O app roda local; o Auth é um serviço na
   nuvem usado mesmo assim. A IA segue local (Ollama) no beta.
4. **Web primeiro, quando for hospedar.** Ao hospedar, valida com web (link + PWA +
   login Google — trivial pra leigo). Mobile (RN/Expo) e desktop nativo vêm depois.
5. **Sem pressa, qualidade acima de prazo.** Refatoração é aceitável.
6. **A IA nunca é caminho crítico.** O solver (Python puro, ms) entrega plano bom
   sozinho; a IA é tempero e degrada com `ia_indisponivel: true`. Custo da IA é opcional.
7. **1 pessoa = 1 conta.** App pessoal; sem workspaces/times.
8. **O moat é o motor de planejamento adaptativo** (solver + diretrizes de IA +
   preferência revelada), não o calendário.
9. **Propriedade de segurança não depende de lembrar.** Ao proteger um invariante
   (ex.: *"nenhuma consulta devolve linha de outro dono"*), escolha o mecanismo pela
   força — nesta ordem, e **nunca abaixo de "falha no teste"**:

   | Força | Mecanismo |
   | --- | --- |
   | Impossível por construção | o estado errado não é representável |
   | Falha no boot | erro ao subir a aplicação |
   | **Falha no teste** | ← piso aceitável (ex.: parâmetro obrigatório ⇒ `TypeError`) |
   | Falha na revisão | alguém precisa reparar no diff |
   | Convenção documentada | ninguém garante nada |

   Corolário: **UUID difícil de adivinhar não é fronteira**, é obscuridade. Se a única
   coisa entre um usuário e o dado de outro é não saber o id, não há isolamento.

**Legenda:** ✅ feito · 🔜 próximo/ativo · ⏸️ parado (não bloqueado) · ⏳ depois ·
💡 decisão em aberto

> **Este arquivo é o mapa do que falta.** Para o que **já está construído** — com os
> arquivos onde cada coisa mora, e a lista do que ainda não existe — veja
> **"Estado atual"** no `CLAUDE.md`.

---

## Onde estamos (19/08/2026)

**Duas fases correm em paralelo, e é de propósito** — a Fase 1 sempre foi declarada
paralela à Fase 0. O que mudou é que o trabalho real está todo na Fase 1 há duas
semanas, e vale dizer isso na cara em vez de deixar deduzir.

| Fase | Estado real |
| --- | --- |
| **0A** — provider + empacotamento | 0A.1 ✅ e 0A.3 ✅. **0A.2, 0A.4, 0A.5, 0A.6 ⏳** |
| **0B** — contas | PR0 ✅, PR1 ✅. **PR2 ⏸️ parado desde 08/08** (desbloqueado, falta o plano). PR3 ⏳ |
| **1.1** — parâmetros por tarefa | PRs A, B, C, C2 ✅. **PR D ⏸️** travado nas decisões D6/D7 |
| **1.2** — conteúdo por ocorrência | PR A ✅, PR B ✅. **PR C 🔜 próxima task**, D ⏳ |
| **1.3a** — criar evento pela UI + `data_fim` obrigatório | gate respondido, **pronta para começar**. Backend + frontend, dois agentes |
| **1.3b** — vocabulário e distinção visual | ⏳ depois da 1.3a. Frontend |

**Onde paramos:** fim do PR B da 1.2 (19/08/2026), ciclo completo e suíte verde (531
testes). **Próxima task:** PR C da 1.2 — transcrever os 3 PDFs e migrar os dados. É a
primeira da 1.2 que **muda o calendário real**.

**O que a Fase 0 ainda deve, para o beta acontecer:** o PR2 (auth de verdade), o PR3
(conta demo), a 0A.2 (profiles do compose) e a 0A.4 (launcher). Sem esses quatro não há
o que entregar a um testador. Nenhum deles está bloqueado por terceiro.

**A ordem entre as duas frentes foi decidida em 19/08/2026: fechar a 1.2 primeiro**, e
o PR B já entrou. O registro da decisão está em "Ordem: fechar a 1.2 ou voltar para a
Fase 0", no fim deste arquivo.

---

## Método de trabalho (por task)

*Revisado em 19/08/2026. Detalhe em `CLAUDE.md` ("Convenção de trabalho"), que é a
fonte da verdade — as skills que executam este ciclo são locais da máquina do autor e
não fazem parte do repositório.*

**1 task = 1 PR**, em três fases separadas por **dois gates humanos**:

**Abrir** — 1. pegar o próximo item daqui · 2. **analisar contra o código e o banco
real** (medir, não estimar) · 3. escrever o plano em `docs/tasks/` **ou** perguntar no
chat · **🚦 gate 1: desenho aprovado**.

> Quando escrever plano: a task mexe em **schema**, **apaga ou sobrescreve dado real**,
> tem **mais de uma forma defensável**, ou **inverte um default**. Fora disso, vai
> direto ao código com as dúvidas no chat. O agente escolhe e justifica; o usuário pode
> discordar.

**Fazer** — 4. implementar · 5. conferir se a suíte cobre a mudança e **escrever o que
faltar** · 6. suíte + `ruff` + `black` + `makemigrations --check` verdes · 7.
**verificar o que só o agente consegue verificar**: dado real em transação com
rollback, leitura pela agenda (não pelo model), fuso, contagens.

> **Se o agente consegue verificar, o agente verifica.** O usuário não roda query, não
> conta linha, não confere fuso. Mandar ele conferir o que um teste provaria é empurrar
> trabalho.

**Fechar** — 8. relatório (o que mudou, as decisões, os números do que já foi provado)
+ **roteiro de teste humano**: o que rodar, o que era para acontecer, o que seria sinal
de problema · **🚦 gate 2: o usuário testa backend e frontend e confirma** · 9. PR · 10.
atualizar **todos** os contextos — `contexto-<task>.md`, `docs/tasks/README.md`, este
arquivo, `CLAUDE.md` e, se mudou algo fora do código, o `HANDOFF.md`.

---

## Fase 0 — Beta técnico com contas (Docker, não-hospedado + Supabase Auth)  ⏸️  *(aberta, sem trabalho desde 08/08 — ver "Onde estamos")*

Objetivo: amigos técnicos rodando em **hardware variado** pra (a) feedback de produto/UX,
(b) decidir **IA local vs API** com dado real, e (c) **testar contas + segurança do Auth**.

> **Ordem decidida (24/07/2026): a 0B é a espinha, a 0A é encaixe.** A 0B tem **custo
> de atraso** — o `dono` atravessa 8 models, ~340 linhas de serializers, ~820 de views,
> ~3.800 de testes, 2 seeds e o servidor MCP; toda feature escrita antes dele vira
> trabalho a mais dentro da 0B (é o princípio nº 2 aplicado). A 0A não cresce com o
> tempo: são 3 pontos de chamada, e o padrão já existe pronto. Logo, **começar pelo
> PR0 da 0B** e encaixar a 0A entre PRs / enquanto a 0B estiver bloqueada por Supabase.

### 0A — Provider trocável + empacotamento
- **0A.1 Abstração `LLMProvider`** ✅ **feito** — novo `services/llm.py` com
  `gerar_json(system, messages, schema) -> dict`, providers `_OllamaProvider` /
  `_AnthropicProvider` / `_MockProvider` e factory por `LLM_PROVIDER`
  (`ollama|anthropic|mock`, default `ollama`). Os 3 pontos que instanciavam
  `ollama.Client` direto (`planejamento_ia.gerar_melhoria`, `cenarios.gerar_cenarios_ia`
  e `refinar_cenario_ia`) passaram a chamar `llm.gerar_json`. `validar_diretrizes`
  segue independente do provider. Decisões (ver [`contexto-0a1-llmprovider.md`](docs/tasks/contexto-0a1-llmprovider.md)):
  **2 envs mantidos** (`AGENTE_PROVIDER` × `LLM_PROVIDER` — formas e necessidades
  distintas); **OpenAI adiado** (YAGNI); `OllamaIndisponivel` vira alias de
  `LLMIndisponivel`; `LLM_MODEL` obrigatório no path anthropic; **sem mudança de
  contrato HTTP**.
- **0A.2 Empacotamento local:** auto-pull do modelo no boot + **profiles do compose**
  (`--profile local` sobe Ollama; `--profile api` não sobe).
- **0A.3 Instrumentação** ✅ **feito** (08/08/2026) — `services/telemetria.py`: um
  registro JSONL por chamada de IA com duração, tokens (entrada/saída), `tok_s` e
  carga de modelo separada. Cobre as 4 famílias (`planejar_ia`, `cenarios`, `refino`
  e **`agente`**, que não era medido por nada). Decisões (ver
  [`contexto-0a3-instrumentacao.md`](docs/tasks/contexto-0a3-instrumentacao.md)):
  tokens **também no modo local** (o Ollama devolve; sem isso não há tok/s para a
  0A.5/0A.6, então excede o "(modo api)" que este item dizia); **`tempos.py`
  intocado** (mede o job, não a chamada); **nada de conteúdo no registro**, nem
  atrás de flag; sem mudança de contrato HTTP.
- **0A.4 Launcher cross-platform:** `start.*`/`stop.*` (mac/linux/windows) + README de
  testador. Pré-requisito: Docker (aceitável pra técnico).
- **0A.5 Teste de tamanho de modelo:** incluir `qwen2.5:3b` na matriz.
  > **Dado já coletado (24/07/2026), fora da matriz:** `qwen2.5:3b-instruct` fechou um
  > plano completo em ~50s em **CPU pura**, com `ia_indisponivel: false`. Foi teste
  > solto — sem a instrumentação da 0A.3 e sem comparação com o 7b — mas é dado real e
  > fica aqui para não se perder.
- **0A.6 IA remota na LAN + host sempre-ligado** ⏳ — adiado em 08/08/2026 por falta de
  hardware em mãos. **Metade da premissa caiu (19/08/2026): o desktop com a RX 7600
  passou a ser a máquina de desenvolvimento** — é onde a stack roda desde 14/08, com o
  compose versionado e sem override (ver §2 do `HANDOFF.md`). Faltam duas coisas, e a
  segunda é dura: o **host sempre-ligado** (o Raspberry Pi, ainda não acessível) e o
  **PR2** — pelo princípio 9, servir a API na LAN sem autenticação nenhuma não é opção,
  e é o caveat que já estava escrito no fim deste item.

  A ideia é separar **onde a IA pensa** de **onde o app roda**: Ollama no desktop com a
  **RX 7600** (o `docker-compose.yml` já é escrito para exatamente essa placa —
  `ollama/ollama:rocm`, `/dev/kfd`, `HSA_OVERRIDE_GFX_VERSION=11.0.0` para o gfx1102; o
  README registra 7,4 → 47 tok/s na migração para GPU), e o resto da stack (`db`,
  `redis`, `web`, `celery`, `mcp`) num host **sempre ligado**, com o Raspberry Pi 4 como
  candidato. Os dois se falam por `OLLAMA_BASE_URL` na LAN.

  **O Pi não serve como host do modelo** — estimativa por banda de memória, não medição:
  geração de token em modelo quantizado é limitada por banda, e a LPDDR4 do Pi 4
  (~4–6 GB/s, sem acelerador que o Ollama use) com o `qwen2.5:3b-instruct` Q4 (~1,9 GB)
  fica na casa de 2 tok/s. O gargalo pior é o *prompt*: o `construir_contexto` manda
  carga por dia, capacidade livre por deadline e fatores por classe, e prompt processing
  no Cortex-A72 é lento. Um plano viraria minutos; cenários, que a semente do
  `services/tempos.py` já estima em ~4× o planejar-ia, viraria dezenas. O 7b não cabe.
  **Serve como host do app** (Postgres + Redis + Django single-user é carga leve), não do
  modelo.

  **O que torna o arranjo viável é o princípio 6**, que já está no código: desktop
  desligado ⇒ `LLMIndisponivel` ⇒ plano base do solver com `ia_indisponivel: true`. O
  planejamento não fica refém de a outra máquina estar ligada — perde só a camada que
  suaviza picos.

  **Config validada em 08/08/2026** (confere em `docker compose config --services`): no
  host do app, um override remove o serviço e as dependências —
  `ollama: !reset null` mais `depends_on: !override` em `web` e `celery`, deixando só
  `db`/`redis` saudáveis; no desktop, `OLLAMA_HOST=0.0.0.0:11434` (o default escuta só
  em `127.0.0.1`) e `OLLAMA_KEEP_ALIVE=-1`.

  **Caveat, pelo princípio 9:** servir o app na LAN **antes do PR2** expõe uma API sem
  autenticação nenhuma à rede. "Está na minha rede" não é fronteira. Ou esta task espera
  o PR2, ou entra com restrição de acesso real (Tailscale/WireGuard, ou bind sem
  publicar a porta). Não misturar as duas coisas por conveniência.

  Rende de brinde o **dado de GPU real** que a Fase 2 espera para decidir IA local vs API.

### 0B — Contas + autenticação (fundação, puxada pra frente)

**Quebrado em 4 PRs** — a 0B inteira num PR é grande demais, e os dois primeiros
**não dependem do Supabase** (dá pra começar já):

| PR | Escopo | Bloqueio externo |
| --- | --- | --- |
| **PR0** | **Views finas + agente em processo** (0B.9) — pré-requisito estrutural, ver abaixo | ✅ **feito** |
| **PR1** | `Perfil` + `dono` + **default invertido** + unicidade por-dono + seed por-usuário (0B.3–0B.6, 0B.10) — enquanto não há JWT, um **perfil local default** resolve o `request.user` | ✅ **feito** |
| **PR2** | `SupabaseJWTAuthentication` + provisionamento JIT (0B.1–0B.2) — troca só *quem* resolve o `request.user`; fica estreito porque o PR1 já isolou tudo. Herdou do PR1 a **credencial de serviço do MCP**: não há o que autenticar antes de existir autenticação | ⏸️ **parado desde 08/08/2026** — o pré-requisito externo **foi cumprido** (projeto Supabase criado e assinatura verificada ponta a ponta); falta o **plano de implementação** |
| **PR3** | Conta demo semeada + gate `pode_usar` stub (0B.7–0B.8) | depende do PR2 |

> **Por que um PR0.** A análise do PR1 (`docs/tasks/fase0b-pr1-dono.md`) mostrou que
> os problemas encontrados não eram 4 bugs independentes, e sim **um default errado**:
> toda consulta nasce global e a segurança depende de alguém lembrar de escopar, 32
> vezes seguidas, para sempre. Em vez de remendar os 32 pontos, o desenho passa a
> **inverter o default** (ver 0B.10) — e isso exige antes que o agente pare de ser
> cliente HTTP de si mesmo (0B.9), senão ele continua atravessando a fronteira de auth
> sem necessidade. Misturar as duas coisas num PR só produziria um diff irrevisável.

- **0B.1 Supabase Auth** (projeto compartilhado, na nuvem): login **Google + email**;
  frontend usa `supabase-js` só pro login e manda o JWT ao Django.
- **0B.2 `SupabaseJWTAuthentication`** (DRF): valida o JWT (segredo/JWKS) + **provisiona
  o Perfil (JIT)** no 1º acesso.

  > ⚠️ **O PR2 tem metade de frontend, e ela não é opcional.** Login via `supabase-js` +
  > `Authorization: Bearer` em toda chamada. Como o passo 10 do ciclo só fecha com o
  > frontend verde, **o PR2 não pode ser planejado como task só-de-backend** — o plano de
  > implementação tem de sair já com o prompt de sincronia desenhado. (Anotado em
  > 24/07/2026 e recuperado em 19/08/2026 de um commit que ficou fora do `main`.)
- **0B.3 `Perfil`/`Conta`** — ✅ feito no PR1. **PK é um UUID local** e o id do Supabase
  mora em `supabase_id`, coluna à parte (decisão Q3): 8 FKs apontam para a PK, então
  trocá-la no PR2 seria reescrever 8 tabelas com dados dentro. Campos: `email`, `nome`,
  `plano`, `trial_ate`.
- **0B.4 `dono = FK(Perfil)`** — ✅ feito no PR1, nos 8 models-raiz (Classe, Tarefa,
  Evento, RegraRecorrencia, PesoPreferencia, EscolhaCenario, RegistroExecucao,
  FeriadoLocal). Só `Ocorrencia` herda pelo pai.
- **0B.5 Unicidade por-dono** — ✅ feito no PR1 nos **três** casos: `Classe.nome`,
  `PesoPreferencia.metrica` e `FeriadoLocal`. Os serializers gravam `dono` do request,
  **nunca do cliente**, e o `queryset` dos `PrimaryKeyRelatedField` é escopado por
  requisição (`ClasseDoDonoField`) — sem isso, um usuário anexa a **classe de outro**
  ao próprio evento e o DRF responde 201, porque para ele o id existe.
  `FeriadoLocal` ficou **por-dono** (decisão Q5); catálogo global com município fica
  para quando houver seleção de município.
- **0B.6 Seed das 5 classes padrão** — ✅ feito no PR1: saiu da migration `0002` para
  `services/perfis.seed_classes_padrao()`, por perfil. O PR2 o chama no provisionamento JIT.
- **0B.9 Views finas + agente em processo** (**PR0**) — ✅ **feito**. *O texto abaixo
  descreve o problema como ele era, antes do PR0.* `services/agente.py` era **código
  Django fazendo HTTP para o próprio Django**: montava a URL a partir de `API_BASE_URL`
  e saía pela rede para chegar onde já estava — atravessando auth, serialização e o
  ciclo de request. Com a auth, tomaria **401**, e "resolver" isso significaria pôr
  credencial de usuário na fila do Celery (o Redis do compose não tem senha e persiste
  em disco).

  **Decisão: as ferramentas do agente passam a chamar os services em processo.** O
  `dono_id` já vem no payload da task — sem token, sem 401, sem expiração, e mais
  rápido. O **MCP server continua HTTP** e continua precisando de credencial: ele é
  container separado servindo clientes externos, então ali a fronteira é legítima e
  fica estreita.

  Pré-requisito (também já feito): `promover` e `planejar` tinham regra de negócio
  **dentro da view** — criavam `Evento` e atualizavam `Tarefa` inline, sem service.
  Desceram para `services/tarefas.py` — que é o que o `CLAUDE.md` já declara como arquitetura
  ("DRF fino: as views delegam para `planner/services/`"). O PR0 não inventa regra
  nova; faz o código cumprir a que já está escrita.

- **0B.10 Inverter o default: acesso global vira explícito** (**PR1**, decisão central) — ✅ feito.
  Deu retorno imediato: ligado o manager, a suíte apontou sozinha **86 falhas**, cada uma
  num ponto que precisava de escopo — inclusive os que a análise não tinha achado.
  O manager default dos 8 models-raiz **exige escopo** — `Evento.objects.all()`
  levanta. Quem precisa do global escreve `Evento.objects.sem_escopo()`, que é
  **grepável e aparece na revisão** (usam isso os seeds e o admin; mais ninguém).

  Junto: **identidade é parâmetro do domínio, nunca ambiente.** Os ~17 pontos de
  service recebem `dono` como argumento **obrigatório** — nada de `contextvar`. O
  motivo é o worker: metade das chamadas nasce fora de um request, e com contexto
  implícito o `dono` vem `None` no Celery, o filtro não acontece e o solver passa a
  tratar a agenda de **todos** como "ocupado" — sem exceção e sem log. Pior: **a suíte
  não pega**, porque roda com um perfil só, onde "global" e "do dono" são o mesmo
  conjunto. Com parâmetro obrigatório, é `TypeError` na primeira execução.

  E **jobs assíncronos carregam o dono** no payload, na chave de cache e no resultado;
  os endpoints que dereferenciam um `job_id` conferem posse. Hoje eles devolvem a quem
  apresentar o `job_id` — o que inclui títulos de tarefas e a agenda inteira.
- **0B.7 Conta default de teste + signup:** um usuário demo (credenciais compartilhadas)
  com `seed_demo` no escopo dele, pra o testador entrar e mexer na hora; **e** criação de
  contas novas próprias (testa signup + isolamento entre contas).
- **0B.8 Gate de pagamento stub:** `plano` + `pode_usar(feature)` sempre `True` (costura
  pronta, cobrança desligada).

> **Arquitetura do beta.** App roda local por testador; **Supabase provê o Auth**. Os
> **dados de domínio** ficam no **Postgres local do compose por testador** ✅ (decidido):
> mantém a IA local, **sem credencial de DB compartilhada** nas máquinas dos testadores —
> a postura de segurança mais limpa, justo o que eles vão avaliar. Ainda testa auth,
> criação de conta e isolamento entre contas (via múltiplas contas na mesma máquina).
> Ponto abdicado: não há visibilidade central da atividade dos testadores. O código de
> auth/`dono` é **idêntico** ao do produto hospedado — muda só o `DATABASE_URL` (Fase 3.1).

> **Reconsiderada e mantida em 24/07/2026 — "tudo no Supabase" foi avaliado e recusado.**
>
> A ideia era pôr também os **dados** no Supabase, não só o Auth. Ela não sobrevive
> porque três coisas que esta fase quer não cabem juntas — **escolha duas**:
>
> | Montagem | Dados centrais | App + IA local em vários PCs | Isolamento como fronteira real |
> | --- | :---: | :---: | :---: |
> | Backend hospedado | ✅ | ❌ | ✅ |
> | App local + Supabase direto | ✅ | ✅ | ❌ |
> | **Esta (Auth no Supabase, dados locais)** | ❌ | ✅ | ✅ |
>
> **Por que a linha do meio não serve.** Apontar o `DATABASE_URL` local para o Supabase
> é uma variável de ambiente — funciona hoje, sem código. Mas põe a senha do Postgres no
> `.env` de cada testador, e o Django conecta com **um papel só, de acesso total**.
> Qualquer testador abre um `psql` e lê os dados de todos. Vale registrar com clareza:
> **o isolamento do PR1 é da camada de aplicação** — protege contra o *código* esquecer
> de filtrar, não contra quem tem a credencial. Pelo princípio 9, fraco demais.
>
> **Por que a primeira não serve.** Hospedar resolve a credencial, mas mata o objetivo
> (b) da fase: sem app na máquina do testador não há **hardware variado** onde medir a
> IA local. E "IA local" implica "Django local" — `montar_plano` chama
> `intervalos_ocupados`/`validar_tarefas` e `construir_contexto` lê pesos e fatores do
> banco; separar os dois é reescrever o pipeline.
>
> **RLS ficou perto.** Um papel do Postgres **por testador** + Row Level Security
> (`USING (dono_id IN (SELECT id FROM planner_perfil WHERE db_role = current_user))`)
> daria as três: a credencial do testador só enxerga as linhas dele, mesmo por `psql`.
> O contra-argumento usual — claims por conexão brigando com pooling, workers sem
> request — **não se aplica aqui**: um testador é uma máquina, um papel, uma conexão
> fixa; a identidade *é* a string de conexão. O que pesou contra foi o custo: onboarding
> manual por testador (criar papel + perfil), migrations centralizadas, políticas com
> `FORCE ROW LEVEL SECURITY` em todas as tabelas (inclusive as internas do Django), e o
> **0B.7 ficaria torto** — ele quer o testador criando várias contas, e aí o isolamento
> entre as contas *dele* voltaria a ser de aplicação. É infraestrutura de verdade para
> uma fase temporária. **Reavaliar se um dia houver dados centrais com app local.**

---

## Fase 1 — Dogfooding + fechar regras de negócio  🔜  *(ATIVA na prática — paralela à Fase 0)*

- Usar de verdade (você + testadores) e **fechar a lista de regras de negócio a mudar**.
- Como o `dono` já entrou (Fase 0B), mudanças de regra **sobem por cima** do schema
  multi-tenant — retrabalho leve aceito.

### Regras já fechadas pelo uso

- **1.1 Parâmetros de agendamento por tarefa** — plano
  [`fase1-parametros-por-tarefa.md`](docs/tasks/fase1-parametros-por-tarefa.md), gate
  respondido em 15/08/2026. **PR A ✅ feito**
  ([contexto](docs/tasks/contexto-fase1-pra.md)), **PR B ✅**
  ([contexto](docs/tasks/contexto-fase1-prb.md)) **PR C ✅**
  ([contexto](docs/tasks/contexto-fase1-prc.md)) e **PR C2 ✅**
  ([contexto](docs/tasks/contexto-fase1-prc2.md)); **PR D ⏸️** travado nas decisões
  **D6/D7**, que são de **produto** (mandar a agenda para uma API remota), não técnicas —
  nenhuma quantidade de código as destrava.

  > **A task entregou, mas o bug que a motivou está corrigido pela metade.** Conferido
  > no banco real em 19/08/2026: das **41 tarefas, 9 têm `estrategia=TARDE` e 32 estão
  > sem estratégia nenhuma**. É consequência direta da decisão D2 (não há default): quem
  > não passou pelo `manage.py marcar_estrategia` segue sendo agendado o quanto antes.
  > Enquanto o frontend não mostrar o campo e o modelo local não preencher, essa conta se
  > fecha por comando, tarefa por tarefa.

  > ⚠️ **O PR C entrega mecanismo, não valor ainda.** Medido em 15/08/2026: o
  > `qwen2.5:7b` **ignora** a instrução de traduzir a observação do usuário —
  > emite só os knobs antigos, e alucinou id nas duas tentativas (o guarda-corpo
  > descartou). A camada de texto livre só rende com modelo mais forte, o que
  > amarra a utilidade dela à decisão **D6**.

  Primeira regra fechada por dogfooding real: **estudo de prova tem de ficar colado na
  prova**. O solver é guloso EDF a partir do `agora`, então agendou o estudo de uma
  prova de 20/10 para **17/08** — conteúdo que nem foi dado, e esquecido na data da
  prova. `buffer_dias` não resolve: ele só *antecipa*, nunca segura para mais tarde.

  O plano dá à `Tarefa` parâmetros **ocultos** (não digitados pelo usuário, ausentes do
  frontend): `estrategia` `CEDO`/`TARDE`, janela e dias da semana por-tarefa,
  `nao_antes_de`, e um campo `observacao` em português que a IA **traduz** para esses
  parâmetros — nunca aplicado às cegas. Inclui o buraco estrutural que o dogfooding
  achou: `simular_plano` aceita `a_partir_de` e não persiste; `_replanejar` persiste e
  não aceita. Corte em 4 PRs; o **PR A resolve o bug sem IA nenhuma**.

  Duas regras de produto que saem daqui e valem além desta task: a IA **pergunta**
  quando fica em dúvida (como *dado* no resultado, nunca bloqueando o plano — princípio
  6), e **nunca usa linguagem técnica** com o usuário (nem nome de campo, nem UUID —
  garantido por tabela de tradução + teste, não por prompt; ver princípio 9).

- **1.2 Aula é bloco fixo; conteúdo, prova e entrega são da ocorrência** — plano
  [`fase1-aula-fixa-conteudo-por-ocorrencia.md`](docs/tasks/fase1-aula-fixa-conteudo-por-ocorrencia.md),
  gate respondido em 18/08/2026. **PR A ✅** ([contexto](docs/tasks/contexto-fase1-2-pra.md))
  e **PR B ✅** ([plano](docs/tasks/fase1-2-prb-importador.md) ·
  [contexto](docs/tasks/contexto-fase1-2-prb.md)); **PR C 🔜 próxima task** (transcrever
  os PDFs + migrar os dados) e **PR D** (edição pela UI) ⏳.

  > **O PR B inverteu um default, e vale saber por quê.** A pergunta do gate era o que
  > fazer com aula marcada em dia de feriado; a resposta foi "acontece". Isso não cabia
  > com `ignorar_feriados=True`, que é o que as séries lançadas à mão usam — o flag faz
  > a data ser pulada e a aula sumiria. Então **a série importada usa
  > `ignorar_feriados=False` e o JSON passa a ser a autoridade sobre quais datas têm
  > aula**: o comando pula toda data que a regra gera e o arquivo não lista. O
  > calendário passa a bater exatamente com o planejamento de ensino, ao custo de uma
  > data esquecida na transcrição virar "sem aula" em silêncio (por isso o relatório
  > conta as auto-puladas).

  > **O dado errado ainda está no banco.** Conferido em 19/08/2026: classe `Aula` tem 62
  > eventos, **58 deles avulsos**; classe `Prova` tem **9, todos avulsos**. O PR A criou
  > o *lugar* onde o conteúdo de uma data mora; quem **move** os 67 para lá é o PR C, e
  > ele depende do importador do PR B. Até lá o calendário segue como estava.

  Segunda regra fechada por dogfooding: **a aula não é uma sequência de eventos
  avulsos**. As 3 disciplinas cujo PDF de planejamento de ensino foi lançado à mão
  viraram 58 eventos por semana + 9 provas como evento próprio, e a série recorrente
  sumiu — junto com a ideia de "compromisso fixo". O que varia por semana é o
  **conteúdo daquela data**, e ele passou a morar na `Ocorrencia`.

  Duas consequências que valem além desta task: o **dia de prova se destaca pela cor da
  classe** (a regra que o frontend já declarava, em vez de um tratamento visual novo), e
  **importar um planejamento de ensino vira um comando** — o que importa porque os PDFs
  das outras 4 disciplinas ainda vêm, e lançar à mão é o que produziu o erro.

- **1.3 Evento × Tarefa: tornar a separação visível** — plano
  [`fase1-3-eventos-e-tarefas-na-ui.md`](docs/tasks/fase1-3-eventos-e-tarefas-na-ui.md),
  **gate respondido em 19/08/2026 (§5)**. Cortada em duas:

  | | Entrega | Repos | Estado |
  | --- | --- | --- | --- |
  | **1.3a** | criar evento pela UI (recorrência, data limite, feriados); `data_fim` obrigatório no banco; Topbar enxuta | **backend + frontend** | 🔜 pronta para começar |
  | **1.3b** | "Inbox" vira "Tarefas"; distinção visual do bloco planejado | frontend | ⏳ |

  **A 1.3a é a primeira task de dois repos** sob a metodologia de dois agentes em
  paralelo — o §6 do plano é o contrato entre eles.

  Terceira regra fechada por dogfooding, e a mais desconfortável: em 19/08/2026 o
  usuário **propôs a separação evento × tarefa como se fosse nova** — sendo que ela é a
  arquitetura desde o MVP (`Evento` é ocupado e nunca é planejado; `Tarefa` tem prazo,
  passa pelo solver e vira bloco ao ser promovida). **O modelo está certo e a interface
  não o conta.**

  O que a análise achou, medido em 19/08/2026:

  - **Não existe caminho para criar evento na UI.** `store/apiStore.jsx:211` tem
    `addEvento` ligado à API, e **nenhum componente o chama** — só existe
    `NovaTarefaForm`. Evento só nasce por promoção de tarefa, agente, API ou admin.
    É a explicação de por que as 3 disciplinas com PDF viraram 58 avulsos.
  - **`ignorar_feriados` e `data_fim` trafegam inteiros** (serializer, mappers, tipos,
    expansão local, testes) e **não têm um único controle na tela**.
  - **Há séries infinitas em produção:** as duas da academia estão com `data_fim` nulo.
  - **Nada distingue bloco planejado de bloco fixo**, embora `origem_tarefa` venha no
    payload.

  Sai daqui uma consequência que vale além da task: **quando o usuário descreve como
  novidade algo que o sistema já faz, o problema é de interface, não de modelo** — e o
  lugar de consertar é a UI, não o schema.

---

## Fase 2 — Decisão da IA  💡  *(com dado da Fase 0)*

- Local é viável no hardware dos testadores? Se **não**, adotar **API comercial**
  (Haiku-class / GPT-mini / Gemini Flash) como default quando hospedar.
- Modelar custo (tokens × preço; por usuário ativo/mês). Manter o local como opção
  "offline/privacidade" via o mesmo `LLMProvider`.
- **Terceira via — híbrido: API para entender, local para calcular.** A decisão está
  escrita acima como binária, e o dogfooting de 15/08/2026 sugere que ela não é. Medido
  no 7b local: ele **executa** bem (instrução explícita e única → ferramenta correta, 4
  argumentos certos) e **compõe** mal (pedido em linguagem natural com ~10 consultas +
  ~9 criações → zero ferramentas, disciplinas inventadas, datas deslocadas em 3 dias,
  UUID vazado na resposta ao usuário). O arranjo que isso sugere: **modelo forte só na
  conversa**, emitindo um JSON único de parâmetros que passa pelo **mesmo**
  `validar_diretrizes`; **solver local** fazendo o cálculo. Conversa é rara e
  planejamento é frequente, então o custo fica baixo. Contrapartida real: a agenda sai
  da máquina — decisão de produto, não detalhe técnico. Desenho em
  [`docs/tasks/fase1-parametros-por-tarefa.md`](docs/tasks/fase1-parametros-por-tarefa.md) §3.6.
- **Dado de GPU real — já existe** (corrige a nota anterior, que dizia "até lá a única
  medição de IA local é em CPU"): a migração para ROCm foi medida em **7,4 → 47 tok/s**
  (commit `cc2a130`), e a telemetria da 0A.3 confirmou **~46,5–46,8 tok/s** por chamada
  do agente em 15/08/2026. O que a **0A.6** ainda deve é o cenário *LAN* (IA no desktop,
  app noutro host), não a medição de GPU.
- **Teto do modelo local nesta máquina:** `8176 MiB` de VRAM. O `qwen2.5:7b` Q4 (4,7 GB)
  cabe; um 14b Q4 (~9 GB) derrama para a CPU e perde os 47 tok/s. Subir de modelo
  **localmente** está fora sem trocar de placa — "modelo forte" significa remoto.

---

## Fase 3 — Endurecer a fundação p/ hospedar  ⏳  💡 *(gatilho: decidir hospedar / trazer leigos)*

O grosso da fundação já foi no beta (Fase 0B). Aqui fica o que é específico de hospedar:

- **3.1 Migrar `DATABASE_URL` p/ Postgres do Supabase** como banco único (pooler:
  `CONN_MAX_AGE`, sem server-side cursors). *(Confirmado como o banco do produto.)*
- **3.2 Revisar segurança** pra ambiente público (o Django deixa de rodar na máquina do
  usuário; credenciais saem do cliente).
- **3.3 Pagamento:** posição do gate `pode_usar` pronta pra ligar (Fase 6).

---

## Fase 4 — Enxugar o stack local  ⏳  *(habilita empacotamento nativo; pode andar com Fase 1)*

- `RegraRecorrencia.dias`: `ArrayField` → `JSONField` (libera SQLite fora do Postgres).
- Celery **eager** + cache **locmem** no perfil local (mata o Redis pro single-user).

---

## Fase 5 — Deploy web hospedado  ⏳  *(o produto)*

- Django + Celery + Redis na nuvem (Fly/Railway/Render — 💡 sem pressa) + Supabase +
  proxy da IA + frontend. Landing + onboarding. **PWA** (ícone + "instalar" sem loja).
- A partir daqui, "mandar link" é a distribuição mais fácil — inclusive pra leigo.

---

## Fase 6 — Pagamento real  ⏳

- Stripe na web (assinatura + webhook). Liga o gate `pode_usar`.
- Corte: **free = solver**; **pro = IA (diretrizes, cenários, agente), horizonte mês,
  integrações, histórico**. Preço-âncora BR: ~R$ 14–19/mês ou R$ 99–129/ano; trial 7–14d.

---

## Fase 7 — Integrações  ⏳

- **Google Calendar bidirecional** (eventos → "ocupado" no solver; sessões → Google;
  revisão de OAuth de escopos sensíveis começa cedo).
- **Notion como fonte de tarefas** (database → Inbox). "O Notion guarda, o Planejador
  agenda." Requer as regras de negócio já assentadas.

---

## Fase 8 — Desktop nativo (Tauri)  ⏳  💡 *(só se ainda fizer sentido)*

- Shell Tauri + React + sidecar Django (SQLite). IA via proxy ou download opcional do
  modelo. Saídas: `.exe`/`.dmg`/`.AppImage`/`.deb`. É **a** forma não-hospedada aceitável
  pra leigo. **Caveat:** a web hospedada (Fase 5) pode reduzir a necessidade.

---

## Fase 9 — Mobile (React Native / Expo)  ⏳

- Reusa a API do Django + SDK de Auth do Supabase. Recursos-âncora: **notificação de
  sessão** e widget "o que fazer agora".

---

## Trilha transversal — Distribuição (evolução)

| Estágio | Como | Quando | Público |
| --- | --- | --- | --- |
| **0. Docker + scripts (+ Supabase Auth)** | `docker compose` + launcher por SO | **agora (ativa)** | amigos técnicos |
| **1. Stack enxuto** | SQLite + sem Redis | Fase 4 | prepara o nativo |
| **Fork A — Web hospedada + PWA** | mandar um link | Fase 5 | leigos e maioria |
| **Fork B — Tauri nativo** | `.exe`/`.dmg`/`.AppImage` | Fase 8 | offline/privacidade |

---

## Backlog anotado (não é fase; é o que não pode se perder)

- **O comparador às vezes mostra só 2 cenários.** `MAX_CENARIOS = 4` em
  `services/cenarios.py` **conta a base**, então o usuário vê no máximo 3 alternativas —
  e menos, quando arquétipos coincidem e são deduplicados. Foi reportado pelo frontend em
  24/07/2026 (nota em
  `../../frontend/Planejamento_Frontend/docs/rotina-inteligente/BACKEND-base-fora-do-lote.md`)
  e a conclusão de lá é que **a base deveria ficar fora do lote**, não ocupar uma das 4
  vagas. Vira task de backend quando alguém pegar; ainda **não foi decidido** se o certo é
  tirar a base da contagem ou subir o teto.

  *Recuperado em 19/08/2026:* este item vivia só no commit `e3f8a45`, na branch
  `claude/0b-perfil-dono-e-escopo`, que **nunca foi mergeada** — o PR1 entrou no `main`
  por outro caminho e levou o código, mas não este parágrafo. É o modo de falha que o
  passo 8 do ciclo existe para evitar.

---

## Decisões em aberto  💡

- ✅ **Dados de domínio no beta:** decidido — **Postgres local por testador**;
  Supabase provê **só o Auth**. **Reconsiderado em 24/07/2026** ("tudo no Supabase") e
  **mantido**: dados centrais custariam ou a credencial do banco na máquina de cada
  testador, ou hospedar — e hospedar mata o objetivo (b) desta fase, que precisa de
  hardware variado. O trilema e a avaliação de RLS estão em "Arquitetura do beta".
  O banco vai para o Supabase **ao hospedar** (Fase 3.1), como sempre esteve escrito.
- ✅ **Ordem 0A vs 0B:** decidido (24/07/2026) — **0B primeiro** (custo de atraso do
  `dono`), quebrada em 4 PRs; 0A encaixa entre PRs.
- ✅ **Identidade do agente** (0B.9): decidido — **ferramentas em processo**, sem HTTP e
  sem credencial. O MCP segue HTTP e ganha credencial de serviço própria.
- ✅ **Threading do `dono` nos services** (0B.10): decidido — **parâmetro obrigatório**,
  não `contextvar` (o contexto implícito falha em silêncio no worker Celery).
- ✅ **Identidade do `Perfil`, destino dos dados de dev, `FeriadoLocal` e escopo do
  admin** (Q3–Q6): decididos em 24/07/2026, no PR1 — PK local + `supabase_id` à parte;
  o perfil local **vira** a conta do usuário no 1º login; `FeriadoLocal` por-dono;
  admin global com `list_filter` por dono. Detalhe em `docs/tasks/contexto-0b-pr1.md`.
- ✅ **Unificar `AGENTE_PROVIDER` e `LLM_PROVIDER`** (0A.1): decidido (24/07/2026) —
  **manter separados**. São formas de propósito diferente (multi-turno stateful vs 1
  chamada stateless) e necessidades distintas (agente quer modelo forte remoto;
  planejador roda bem no 7B local); só o vocabulário dos valores foi alinhado.
- **Conversa remota × privacidade** (Fase 1.1 / Fase 2) — o híbrido da Fase 2 manda a
  agenda inteira (matérias, provas, pesos) para uma API. O projeto é ciosa disso em todo
  o resto: a telemetria **nunca** grava conteúdo, nem atrás de flag. Aceitar o envio na
  conversa (mantendo o planejamento local) é decisão de **produto**, não técnica.
- ✅ **Estratégia de agendamento default por classe** (Fase 1.1): decidido no gate de
  15/08/2026 — **não há default**. Nem herdado da classe na criação, nem lido em tempo
  de plano: vale só o que estiver explícito na tarefa. Junto veio o princípio que rege
  os parâmetros novos: **ortogonais**, cada um uma condição específica, sem acoplamento
  implícito — aceita-se que sejam muitos. Foi por isso que `TARDE` **não** usa
  `buffer_dias`: "terminar na véspera" virou `nao_depois_de`, condição própria.
  Consequência a resolver no PR A: sem default, algo precisa ligar o campo nas tarefas
  que já existem — ver §4 e Pendência 1 do plano.
- ✅ **Ordem: fechar a 1.2 ou voltar para a Fase 0** (levantado e decidido em
  19/08/2026) — **fechar a 1.2 primeiro**; o PR B entrou no mesmo dia. O que pesou foi o
  relógio: o semestre roda até 17/12 e o dado de dogfooding só existe com ele vivo,
  enquanto o PR2 não tem prazo. **Depois do PR C, a Fase 0 volta a ser a frente.**
  Trabalhar a Fase 1 agora **não furou a fila**: ela é declarada paralela, e o
  argumento que impunha ordem estrita (o *custo de atraso* do `dono`, princípio 2) foi
  **extinto pelo PR1** — regra de negócio agora sobe por cima do schema multi-tenant, com
  retrabalho leve aceito. A Fase 1 ainda **alimenta** a Fase 0: o bug do estudo de prova
  queimaria a rodada de feedback se fosse distribuído, e a medição do 7b é dado para a
  decisão de IA local vs API.

  O que **não** foi decisão: o PR2 parou por onze dias porque o `docs/tasks/README.md`
  continuou rotulado "🚧 Bloqueado" depois que o Supabase foi provisionado (08/08). O
  rótulo foi corrigido; a escolha de ordem, não.

  | A favor de fechar a 1.2 primeiro | A favor do PR2 primeiro |
  | --- | --- |
  | **Tem relógio**: o semestre roda até 17/12 e o dado de dogfooding só existe com ele vivo | O PR2 **não tem relógio** — o Supabase está provisionado e continua lá |
  | O PR A já foi feito e **ainda não rendeu valor**: sem o importador, só o admin escreve conteúdo | O PR2 é a **espinha**, e é estreito de propósito: essencialmente uma função |
  | Os 67 eventos avulsos seguem errados no calendário que o usuário usa todo dia | O PR2 **destrava a 0A.6** (não se serve API sem auth na LAN) e é pré-requisito do PR3 e do beta |

  Contra o PR2 agora, um custo real: ele põe o uso pessoal diário atrás de um login que
  depende de internet, no meio do semestre.
- **IA local vs API** — aguarda dado da Fase 0.
- **Regras de negócio a mudar** — aguarda dogfooding (Fase 1).
- **Hospedar (Fork A) vs desktop nativo (Fork B)** pros leigos — decidir após o beta.
- **Provider comercial** específico e **modelo local** final (3b vs 7b).
- **Hospedagem** (Fly/Railway/Render) — decidir perto da Fase 5.
