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

**Legenda:** ✅ feito · 🔜 próximo/ativo · ⏳ depois · 💡 decisão em aberto

> **Este arquivo é o mapa do que falta.** Para o que **já está construído** — com os
> arquivos onde cada coisa mora, e a lista do que ainda não existe — veja
> **"Estado atual"** no `CLAUDE.md`.

---

## Método de trabalho (por task)

Cada item deste ROADMAP percorre o mesmo ciclo. **1 task = 1 PR** (refina a
convenção "1 marco = 1 PR" do `CLAUDE.md`):

1. **Task** — pegar o próximo item na ordem definida abaixo.
2. **Análise do código atual** — mapear o que a task toca de verdade (arquivos,
   models, testes, efeitos colaterais) antes de propor qualquer coisa.
3. **Plano de implementação** — nota de design em `docs/tasks/`, com as dúvidas
   e decisões em aberto explicitadas.
4. **Revisão + sanar dúvidas** — o plano é revisado e as dúvidas resolvidas
   **juntos, antes de escrever código**. Nada de implementar sobre premissa não
   confirmada.
5. **Implementação.**
6. **Testes do backend** — suíte + lint + checagem de migrations verdes (ver `CLAUDE.md`).
7. **Documento de contexto** em `docs/tasks/contexto-<task>.md` — o que era para
   fazer, o que foi feito, decisões, bugs encontrados e como corrigidos, e o estado
   em que a próxima task começa. **O contexto do agente é zerado entre tasks**, então
   este documento é o único fio. Índice em `docs/tasks/README.md`.
8. **Prompt de sincronia com o frontend** — o frontend é um repo **vizinho**
   (`../../Frontend/Planejamento_Frontend/`) que consome esta API. Produza um prompt,
   para o usuário repassar ao agente do frontend, com as mudanças de contrato de forma
   acionável (ou a confirmação de que não há nenhuma, com o porquê). Detalhe em
   `CLAUDE.md`, "Passos 8–9".
9. **PR — gated no frontend.** Só depois que o usuário aplicar as mudanças no
   frontend, rodar os **testes end-to-end** lá e confirmar que está tudo verde.
10. Se tudo ok → **próxima task**.

---

## Fase 0 — Beta técnico com contas (Docker, não-hospedado + Supabase Auth)  🔜  *(ATIVA)*

Objetivo: amigos técnicos rodando em **hardware variado** pra (a) feedback de produto/UX,
(b) decidir **IA local vs API** com dado real, e (c) **testar contas + segurança do Auth**.

> **Ordem decidida (24/07/2026): a 0B é a espinha, a 0A é encaixe.** A 0B tem **custo
> de atraso** — o `dono` atravessa 8 models, ~340 linhas de serializers, ~820 de views,
> ~3.800 de testes, 2 seeds e o servidor MCP; toda feature escrita antes dele vira
> trabalho a mais dentro da 0B (é o princípio nº 2 aplicado). A 0A não cresce com o
> tempo: são 3 pontos de chamada, e o padrão já existe pronto. Logo, **começar pelo
> PR0 da 0B** e encaixar a 0A entre PRs / enquanto a 0B estiver bloqueada por Supabase.

### 0A — Provider trocável + empacotamento
- **0A.1 Abstração `LLMProvider`** em `planejamento_ia.py`: `gerar_diretrizes(contexto)
  -> Diretrizes`, com `OllamaProvider` / `AnthropicProvider` / `OpenAIProvider` /
  `MockProvider`, por env (`LLM_PROVIDER=ollama|api|mock`). `validar_diretrizes`
  (guarda-corpo) segue independente do provider. **Default `ollama`** (nada muda pra
  quem roda local).
  > **Mais barato do que parece:** `services/agente.py` (classes `_OllamaProvider` /
  > `_AnthropicProvider` e a factory `_criar_provider`) **já tem** esse padrão
  > (`_OllamaProvider`, provider Anthropic, factory por `AGENTE_PROVIDER`) — só que
  > para a forma *multi-turno com tool use*. Falta estendê-lo à forma *chamada única
  > com JSON schema forçado*, nos 3 pontos que ainda instanciam `ollama.Client` direto:
  > `planejamento_ia.py`, `cenarios.py` (2×) — procure por `ollama.Client(`. Considerar
  > unificar `AGENTE_PROVIDER` e `LLM_PROVIDER` em vez de manter dois envs.
- **0A.2 Empacotamento local:** auto-pull do modelo no boot + **profiles do compose**
  (`--profile local` sobe Ollama; `--profile api` não sobe).
- **0A.3 Instrumentação:** logar tempo de parede real + (modo api) tokens.
- **0A.4 Launcher cross-platform:** `start.*`/`stop.*` (mac/linux/windows) + README de
  testador. Pré-requisito: Docker (aceitável pra técnico).
- **0A.5 Teste de tamanho de modelo:** incluir `qwen2.5:3b` na matriz.
  > **Dado já coletado (24/07/2026), fora da matriz:** `qwen2.5:3b-instruct` fechou um
  > plano completo em ~50s em **CPU pura**, com `ia_indisponivel: false`. Foi teste
  > solto — sem a instrumentação da 0A.3 e sem comparação com o 7b — mas é dado real e
  > fica aqui para não se perder.

### 0B — Contas + autenticação (fundação, puxada pra frente)

**Quebrado em 4 PRs** — a 0B inteira num PR é grande demais, e os dois primeiros
**não dependem do Supabase** (dá pra começar já):

| PR | Escopo | Bloqueio externo |
| --- | --- | --- |
| **PR0** | **Views finas + agente em processo** (0B.9) — pré-requisito estrutural, ver abaixo | ✅ **feito** |
| **PR1** | `Perfil` + `dono` + **default invertido** + unicidade por-dono + seed por-usuário (0B.3–0B.6, 0B.10) — enquanto não há JWT, um **perfil local default** resolve o `request.user` | ✅ **feito** |
| **PR2** | `SupabaseJWTAuthentication` + provisionamento JIT (0B.1–0B.2) — troca só *quem* resolve o `request.user`; fica estreito porque o PR1 já isolou tudo. Herdou do PR1 a **credencial de serviço do MCP**: não há o que autenticar antes de existir autenticação | 🔜 **próxima task** — **exige o projeto Supabase criado** |
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

## Fase 1 — Dogfooding + fechar regras de negócio  🔜  *(paralela à Fase 0)*

- Usar de verdade (você + testadores) e **fechar a lista de regras de negócio a mudar**.
- Como o `dono` já entrou (Fase 0B), mudanças de regra **sobem por cima** do schema
  multi-tenant — retrabalho leve aceito.

---

## Fase 2 — Decisão da IA  💡  *(com dado da Fase 0)*

- Local é viável no hardware dos testadores? Se **não**, adotar **API comercial**
  (Haiku-class / GPT-mini / Gemini Flash) como default quando hospedar.
- Modelar custo (tokens × preço; por usuário ativo/mês). Manter o local como opção
  "offline/privacidade" via o mesmo `LLMProvider`.

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
- **Unificar `AGENTE_PROVIDER` e `LLM_PROVIDER`** num só env (0A.1) ou manter separados.
- **IA local vs API** — aguarda dado da Fase 0.
- **Regras de negócio a mudar** — aguarda dogfooding (Fase 1).
- **Hospedar (Fork A) vs desktop nativo (Fork B)** pros leigos — decidir após o beta.
- **Provider comercial** específico e **modelo local** final (3b vs 7b).
- **Hospedagem** (Fly/Railway/Render) — decidir perto da Fase 5.
