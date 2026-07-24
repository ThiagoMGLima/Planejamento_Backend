# ROADMAP — Planejador de Rotina (de projeto pessoal a produto)

> Esqueleto vivo. Consolida as decisões do brainstorm de produto e marca os pontos
> em aberto. **Não é contrato de implementação** (isso segue nos `PLAN.md` / handoffs
> por marco) — é o mapa macro.

## Princípios que guiam tudo

1. **Beta técnico primeiro, hospedado, JÁ com contas.** Distribui pros **amigos
   técnicos** por **link**; eles testam também o **sistema de contas e o isolamento**.
   Leigos ficam pra depois — o que muda é o público, não mais a arquitetura.

   > **Revisado em 24/07/2026** (era "não-hospedado, cada um roda a própria cópia").
   > Ver "Arquitetura do beta" — a mudança veio de querer **tudo no Supabase**, e
   > banco central com app local poria a credencial do banco na máquina de cada
   > testador.
2. **Fundação de contas entra no beta; regras de negócio sobem por cima.** Como os
   testadores vão exercitar auth/isolamento, o `dono` vem **agora** — antes de as regras
   assentarem. Aceita-se o **retrabalho leve** de re-tocar os models quando as regras
   mudarem (Fase 1).
3. **Uma arquitetura só, do beta ao produto.** Supabase (Auth + Postgres) + backend
   hospedado desde o beta. Não se mantém uma montagem para testar e outra para vender:
   o que os testadores exercitam é o que vai para produção.
4. **Web primeiro.** O beta já é web (link + login). PWA, mobile (RN/Expo) e desktop
   nativo vêm depois.
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
6. **Testes** — suíte + lint + checagem de migrations verdes (ver `CLAUDE.md`).
7. **Documento de contexto** em `docs/tasks/contexto-<task>.md` — o que era para
   fazer, o que foi feito, decisões, bugs encontrados e como corrigidos, e o estado
   em que a próxima task começa. **O contexto do agente é zerado entre tasks**, então
   este documento é o único fio. Índice em `docs/tasks/README.md`.
8. Se tudo ok → **próxima task**.

---

## Fase 0 — Beta técnico com contas (hospedado + Supabase)  🔜  *(ATIVA)*

Objetivo: amigos técnicos usando **por link** pra (a) feedback de produto/UX e
(b) **testar contas e isolamento** numa instalação central de verdade.

> ⚠️ **Escopo revisado em 24/07/2026 — "tudo no Supabase".** O beta era não-hospedado,
> com Postgres local por testador. Agora o Supabase é Auth **e** banco, e o backend
> roda na nuvem (ver "Arquitetura do beta"). Três consequências que mudam esta fase:
>
> - **A decisão IA local vs API sobe para cá** (era Fase 2, "com dado da Fase 0"). Com
>   o backend hospedado não há GPU do testador para medir: ou se paga GPU na nuvem, ou
>   a IA do beta vai por **API comercial**. O objetivo (b) original — decidir com dado
>   de hardware variado — deixou de ser possível nesta fase.
> - **A 0A encolhe.** 4 dos 5 itens existiam para fazer a distribuição local
>   funcionar (ver 0A).
> - **Fases 3.1, 3.2 e parte da 5 são absorvidas aqui.**

> **Ordem decidida (24/07/2026): a 0B é a espinha.** A 0B tem **custo de atraso** — o
> `dono` atravessa 8 models, ~340 linhas de serializers, ~820 de views, ~3.800 de
> testes, 2 seeds e o servidor MCP; toda feature escrita antes dele vira trabalho a
> mais dentro da 0B (é o princípio nº 2 aplicado). Por isso o PR0 e o PR1 vieram
> primeiro.
>
> **Revisto no mesmo dia, com "tudo no Supabase":** a 0A deixou de ser puro encaixe —
> a **0A.1 é pré-requisito do 0C.4** (IA hospedada precisa de provider de API). A ordem
> passa a ser: **0B/PR2 → 0A.1 + 0A.3 → 0C → 0B/PR3**. A 0A.1 continua sendo o que
> fazer enquanto a 0B estiver bloqueada esperando o projeto Supabase.

### 0A — Provider trocável ~~+ empacotamento~~

> **Revisada em 24/07/2026 pela decisão "tudo no Supabase".** Esta seção existia em
> boa parte para fazer a **distribuição local** funcionar; com o beta hospedado, 4 dos
> 5 itens perdem o motivo. Em compensação a **0A.1 deixou de ser encaixe e virou
> caminho crítico**: hospedado, a IA precisa de um provider de API, e é ela que o
> torna trocável.

- **0A.1 Abstração `LLMProvider`** em `planejamento_ia.py`: `gerar_diretrizes(contexto)
  -> Diretrizes`, com `OllamaProvider` / `AnthropicProvider` / `OpenAIProvider` /
  `MockProvider`, por env (`LLM_PROVIDER=ollama|api|mock`). `validar_diretrizes`
  (guarda-corpo) segue independente do provider. **Default `ollama`** (nada muda pra
  quem roda local).
  > **Mais barato do que parece:** `services/agente.py:340-480` **já tem** esse padrão
  > (`_OllamaProvider`, provider Anthropic, factory por `AGENTE_PROVIDER`) — só que
  > para a forma *multi-turno com tool use*. Falta estendê-lo à forma *chamada única
  > com JSON schema forçado*, nos 3 pontos que ainda instanciam `ollama.Client` direto:
  > `planejamento_ia.py:199`, `cenarios.py:199` e `cenarios.py:278`. Considerar
  > unificar `AGENTE_PROVIDER` e `LLM_PROVIDER` em vez de manter dois envs.

  **Default muda para `api` no ambiente hospedado**; `ollama` continua sendo o default
  de desenvolvimento local (o seu compose não muda).
- **0A.2 Empacotamento local** — ⏸️ **sai da Fase 0.** Profiles do compose e auto-pull
  serviam para o testador subir o Ollama na própria máquina. Vira item da Fase 8
  (desktop nativo), se ela acontecer.
- **0A.3 Instrumentação:** logar tempo de parede real + **tokens e custo**. **Sobe de
  prioridade**: com IA por API, isso deixa de ser curiosidade de performance e vira o
  número que decide o preço (Fase 6) e o corte free/pro.
- **0A.4 Launcher cross-platform** — ❌ **cancelado.** `start.*`/`stop.*` e o README de
  testador existiam para quem roda a própria cópia. Hospedado, o testador recebe um
  link. O que sobra é onboarding, que já está na Fase 5.
- **0A.5 Teste de tamanho de modelo (3b vs 7b)** — ⏸️ **adiado, sem virar lixo.** Só faz
  sentido para o fork local/offline (Fase 8) ou se um dia se pagar GPU na nuvem.
  **Dado já coletado (24/07/2026):** `qwen2.5:3b-instruct` fechou um plano completo em
  ~50s em CPU pura, com `ia_indisponivel: false`. Foi teste solto — sem a instrumentação
  da 0A.3 e sem comparação com o 7b — mas é dado real e fica registrado aqui para não
  se perder.

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
- **0B.9 Views finas + agente em processo** (**PR0**). Hoje `services/agente.py:47` é
  **código Django fazendo HTTP para o próprio Django**: monta a URL a partir de
  `API_BASE_URL` e sai pela rede para chegar onde já estava — atravessando auth,
  serialização e o ciclo de request. Quando a auth entrar, toma **401**, e "resolver"
  isso significaria pôr credencial de usuário na fila do Celery (o Redis do compose não
  tem senha e persiste em disco).

  **Decisão: as ferramentas do agente passam a chamar os services em processo.** O
  `dono_id` já vem no payload da task — sem token, sem 401, sem expiração, e mais
  rápido. O **MCP server continua HTTP** e continua precisando de credencial: ele é
  container separado servindo clientes externos, então ali a fronteira é legítima e
  fica estreita.

  Pré-requisito: `promover` e `planejar` têm regra de negócio **dentro da view**
  (`views.py:107-166` cria `Evento` e atualiza `Tarefa` inline, sem service). Precisam
  descer para `services/` — que é o que o `CLAUDE.md` já declara como arquitetura
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
  os 5 endpoints de status conferem posse. Hoje eles devolvem o resultado a quem
  apresentar o `job_id` — o que inclui títulos de tarefas e a agenda inteira.
- **0B.7 Conta default de teste + signup:** um usuário demo (credenciais compartilhadas)
  com `seed_demo` no escopo dele, pra o testador entrar e mexer na hora; **e** criação de
  contas novas próprias (testa signup + isolamento entre contas).
- **0B.8 Gate de pagamento stub:** `plano` + `pode_usar(feature)` sempre `True` (costura
  pronta, cobrança desligada).

### 0C — Hospedagem  *(nova, 24/07/2026 — consequência de "tudo no Supabase")*

Absorve a **Fase 3.1**, a **3.2** e a parte de infraestrutura da **Fase 5**. Vem
**depois do PR2** (sem login, não há o que expor) e pode andar em paralelo ao PR3.

- **0C.1 `DATABASE_URL` → Postgres do Supabase.** Pooler: `CONN_MAX_AGE`, sem
  server-side cursors. Migrar os dados de dev existentes (ou recomeçar do `seed_demo` —
  decidir na hora). A partir daqui **migrations rodam de um lugar só**.
- **0C.2 Deploy do Django + Celery + Redis** (Fly/Railway/Render — 💡 sem pressa).
  Um serviço web + um worker; Redis gerenciado.
- **0C.3 Segurança de ambiente público:** `DEBUG=0`, `ALLOWED_HOSTS`/`CORS` reais,
  HTTPS, secrets fora do repo. **Revisar o servidor MCP**: hoje é um container sem
  autenticação — hospedado, ou fica atrás de credencial de serviço (a herdada do PR1)
  ou não sobe.
- **0C.4 IA por API** (depende de 0A.1 + 0A.3): provider comercial no ambiente
  hospedado, com teto de custo e o `ia_indisponivel` já existente como degradação.
- **0C.5 Frontend hospedado + onboarding mínimo:** o SPA aponta para a API pública, e o
  testador entra por link. Landing e PWA seguem na Fase 5.

> **Arquitetura do beta — "tudo no Supabase"** ✅ *(decidido em 24/07/2026; substitui a
> decisão anterior de Postgres local por testador)*.
>
> **Supabase é Auth E banco**; o backend (Django + Celery + Redis) roda **hospedado**, e
> o testador recebe um **link**. Nada roda na máquina dele.
>
> **Por que a mudança arrasta a hospedagem junto.** A alternativa — app local com banco
> central — não se sustenta: a credencial do Postgres iria no `.env` de cada testador, e
> o Django conecta com **um papel só, com acesso total a todas as tabelas**. Qualquer
> testador abriria um `psql` e leria os dados de todo mundo. Isso vale registrar com
> clareza: **o isolamento do PR1 é da camada de aplicação.** O manager que recusa
> consulta sem escopo protege contra o *código* esquecer de filtrar; ele não é fronteira
> de banco. Com a credencial na mão, o `dono` vira decoração — e pelo princípio 9 isso
> é fraco demais para um invariante de segurança. Hospedando, a credencial fica no
> servidor e o `dono` volta a ser a única porta.
>
> **Descartado:** RLS no Postgres (o banco impondo o isolamento) resolveria com app
> local, e é a resposta nativa do Supabase — mas o Django conecta com um papel fixo, e
> fazer claims por conexão brigando com pooling, mais workers Celery que não têm request
> nenhum de onde tirar identidade, é um projeto à parte que ainda duplicaria a camada
> `dono`. Reavaliar só se um dia houver fork local (Fase 8).
>
> **O que se ganha:** uma arquitetura só do beta ao produto (princípio 3); visibilidade
> central da atividade dos testadores (era o ponto abdicado da decisão anterior);
> backup e migrations num lugar só.
>
> **O que se paga:** custo mensal de hospedagem e de tokens de IA desde o beta; a IA
> deixa de ser local (ver a nota de escopo da Fase 0); e some o argumento "seus dados
> não saem da sua máquina", que passa a ser eventual bandeira do fork Tauri (Fase 8).

---

## Fase 1 — Dogfooding + fechar regras de negócio  🔜  *(paralela à Fase 0)*

- Usar de verdade (você + testadores) e **fechar a lista de regras de negócio a mudar**.
- Como o `dono` já entrou (Fase 0B), mudanças de regra **sobem por cima** do schema
  multi-tenant — retrabalho leve aceito.

---

## Fase 2 — Decisão da IA  ⏩  *(absorvida pela Fase 0 em 24/07/2026)*

> **A pergunta original morreu com a hospedagem.** Era "local é viável no hardware dos
> testadores?" — mas hospedado não há hardware de testador para medir. A decisão virou
> **API comercial no beta** (0C.4), pelo caminho que a 0A.1 abre.

O que sobrevive desta fase e segue valendo:

- **Modelar custo** (tokens × preço; por usuário ativo/mês) — agora com o dado real da
  instrumentação (0A.3), e é o que alimenta o preço da Fase 6.
- **Escolher o provider comercial** específico (Haiku-class / GPT-mini / Gemini Flash).
- Manter o **local como opção "offline/privacidade"** via o mesmo `LLMProvider` — hoje
  isso é o fork Tauri (Fase 8), não mais o beta.

---

## Fase 3 — Endurecer a fundação p/ hospedar  ⏩  *(absorvida pela Fase 0 em 24/07/2026)*

Hospedar deixou de ser um gatilho futuro: acontece no beta (**0C**).

- ~~3.1 Migrar `DATABASE_URL` p/ Postgres do Supabase~~ → **0C.1**.
- ~~3.2 Revisar segurança pra ambiente público~~ → **0C.3**.
- **3.3 Pagamento:** posição do gate `pode_usar` pronta pra ligar (Fase 6) — já é o
  **0B.8**, que segue no PR3.

---

## Fase 4 — Enxugar o stack local  ⏳  *(só serve ao Fork B agora)*

> **Perdeu urgência em 24/07/2026.** Servia para o beta distribuído e para preparar o
> nativo; com o beta hospedado, sobra só o segundo motivo. Se o Fork B (Fase 8) não
> acontecer, esta fase inteira não acontece.

- `RegraRecorrencia.dias`: `ArrayField` → `JSONField` (libera SQLite fora do Postgres).
- Celery **eager** + cache **locmem** no perfil local (mata o Redis pro single-user).

---

## Fase 5 — Produto público  ⏳  *(o produto)*

> **Encolheu em 24/07/2026:** a infraestrutura (Django + Celery + Redis na nuvem +
> Supabase + frontend hospedado) virou **0C**, no beta. O que sobra aqui é o que
> transforma uma instalação hospedada em **produto para leigo**:

- **Landing + onboarding** de verdade (o beta tem só o mínimo do 0C.5).
- **PWA** (ícone + "instalar" sem loja).
- Endurecer para público aberto: signup sem curadoria, limites de abuso, suporte.
- "Mandar link" já é a distribuição desde o beta — aqui ela passa a servir leigo.

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

> **Simplificou em 24/07/2026.** O estágio 0 era "Docker + launcher por SO"; com o
> beta hospedado, **link é a distribuição desde o começo** e o Fork A deixou de ser
> uma bifurcação futura — virou o caminho principal.

| Estágio | Como | Quando | Público |
| --- | --- | --- | --- |
| **0. Web hospedada (Supabase + nuvem)** | mandar um link | **agora (ativa, 0C)** | amigos técnicos |
| **1. Produto público + PWA** | link + landing + onboarding | Fase 5 | leigos e maioria |
| **Fork B — Tauri nativo** | `.exe`/`.dmg`/`.AppImage` | Fase 8 | offline/privacidade |

O **estágio "stack enxuto"** (SQLite, sem Redis — Fase 4) e o **Ollama local** deixam
de servir ao beta e passam a existir só para o Fork B, se ele acontecer.

---

## Decisões em aberto  💡

- ✅ **Ordem 0A vs 0B:** decidido (24/07/2026) — **0B primeiro** (custo de atraso do
  `dono`), quebrada em 4 PRs. *(Revisto no mesmo dia: a 0A.1 deixou de ser encaixe e
  virou pré-requisito do 0C.4.)*
- ✅ **Identidade do agente** (0B.9): decidido — **ferramentas em processo**, sem HTTP e
  sem credencial. O MCP segue HTTP e ganha credencial de serviço própria.
- ✅ **Threading do `dono` nos services** (0B.10): decidido — **parâmetro obrigatório**,
  não `contextvar` (o contexto implícito falha em silêncio no worker Celery).
- ✅ **Identidade do `Perfil`, destino dos dados de dev, `FeriadoLocal` e escopo do
  admin** (Q3–Q6): decididos em 24/07/2026, no PR1 — PK local + `supabase_id` à parte;
  o perfil local **vira** a conta do usuário no 1º login; `FeriadoLocal` por-dono;
  admin global com `list_filter` por dono. Detalhe em `docs/tasks/contexto-0b-pr1.md`.
- ✅ **Dados de domínio e arquitetura do beta:** decidido (24/07/2026) — **tudo no
  Supabase** (Auth + Postgres) e **backend hospedado**. Substitui a decisão anterior
  ("Postgres local por testador"): banco central com app local poria a credencial do
  banco na máquina de cada testador, e o isolamento do PR1 é de aplicação, não de
  banco. Ver "Arquitetura do beta".
- ✅ **IA local vs API:** decidido por consequência — **API comercial** no beta
  hospedado (0C.4). Não havia como manter a pergunta original: sem app na máquina do
  testador, não há hardware variado para medir.
- **Unificar `AGENTE_PROVIDER` e `LLM_PROVIDER`** num só env (0A.1) ou manter separados.
- **Provider comercial** específico (Haiku-class / GPT-mini / Gemini Flash) — decidir
  na 0C.4, com o custo por usuário/mês da instrumentação (0A.3).
- **Onde hospedar** (Fly/Railway/Render) — decidir na 0C.2. Deixou de ser "perto da
  Fase 5"; agora é da Fase 0.
- **Migrar os dados de dev ou recomeçar do `seed_demo`** ao apontar para o Supabase
  (0C.1).
- **Regras de negócio a mudar** — aguarda dogfooding (Fase 1).
- **Fork B (Tauri) ainda faz sentido?** Era a resposta "offline/privacidade"; com tudo
  na nuvem, é a única coisa que sustentaria essa bandeira — decidir após o beta.
