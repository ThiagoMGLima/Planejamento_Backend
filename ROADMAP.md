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
7. Se tudo ok → **próxima task**.

---

## Fase 0 — Beta técnico com contas (Docker, não-hospedado + Supabase Auth)  🔜  *(ATIVA)*

Objetivo: amigos técnicos rodando em **hardware variado** pra (a) feedback de produto/UX,
(b) decidir **IA local vs API** com dado real, e (c) **testar contas + segurança do Auth**.

> **Ordem decidida (24/07/2026): a 0B é a espinha, a 0A é encaixe.** A 0B tem **custo
> de atraso** — o `dono` atravessa 8 models, ~340 linhas de serializers, ~820 de views,
> ~2.600 de testes, 2 seeds e o servidor MCP; toda feature escrita antes dele vira
> trabalho a mais dentro da 0B (é o princípio nº 2 aplicado). A 0A não cresce com o
> tempo: são 3 pontos de chamada, e o padrão já existe pronto. Logo, **começar por
> 0B.1-PR1** e encaixar a 0A entre PRs / enquanto a 0B estiver bloqueada por Supabase.

### 0A — Provider trocável + empacotamento
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
- **0A.2 Empacotamento local:** auto-pull do modelo no boot + **profiles do compose**
  (`--profile local` sobe Ollama; `--profile api` não sobe).
- **0A.3 Instrumentação:** logar tempo de parede real + (modo api) tokens.
- **0A.4 Launcher cross-platform:** `start.*`/`stop.*` (mac/linux/windows) + README de
  testador. Pré-requisito: Docker (aceitável pra técnico).
- **0A.5 Teste de tamanho de modelo:** incluir `qwen2.5:3b` na matriz.

### 0B — Contas + autenticação (fundação, puxada pra frente)

**Quebrado em 3 PRs** — a 0B inteira num PR é grande demais, e o primeiro **não
depende do Supabase** (dá pra começar já):

| PR | Escopo | Bloqueio externo |
| --- | --- | --- |
| **PR1** | `Perfil` + `dono` + isolamento + unicidade por-dono + seed por-usuário (0B.3–0B.6, 0B.9) — enquanto não há JWT, um **perfil local default** resolve o `request.user` | nenhum 🔜 **próxima task** |
| **PR2** | `SupabaseJWTAuthentication` + provisionamento JIT (0B.1–0B.2) — troca só *quem* resolve o `request.user`; fica estreito porque o PR1 já isolou tudo | **exige o projeto Supabase criado** |
| **PR3** | Conta demo semeada + gate `pode_usar` stub (0B.7–0B.8) | depende do PR2 |

- **0B.1 Supabase Auth** (projeto compartilhado, na nuvem): login **Google + email**;
  frontend usa `supabase-js` só pro login e manda o JWT ao Django.
- **0B.2 `SupabaseJWTAuthentication`** (DRF): valida o JWT (segredo/JWKS) + **provisiona
  o Perfil (JIT)** no 1º acesso.
- **0B.3 `Perfil`/`Conta`** (PK = UUID do usuário Supabase; `plano`, `trial_ate`, prefs).
- **0B.4 `dono = FK(Perfil)`** nos models-raiz (Classe, Tarefa, Evento, RegraRecorrencia,
  PesoPreferencia, EscolhaCenario, RegistroExecucao, FeriadoLocal); filhos herdam pelo pai.
- **0B.5 Unicidade por-dono** (`Classe.nome`, `FeriadoLocal` deixam de ser globais);
  mixin de queryset filtrando por `request.user`; serializers gravam `dono` do request,
  nunca do cliente.
  > ⚠️ **`PesoPreferencia.metrica` também é `unique=True` global** (`models.py:145`) e
  > não estava nesta lista. Sem virar unicidade por-dono, o primeiro usuário a gravar
  > um peso **trava o aprendizado de todos os outros**. Revisar `EscolhaCenario`,
  > `RegistroExecucao` e o `uq_feriadolocal_data` com o mesmo olho.
- **0B.6 Seed das 5 classes padrão** vira **por-usuário** (no Perfil, JIT) — não mais global.
- **0B.9 Propagação de identidade para o agente e o MCP** (novo, entra no PR1).
  `services/agente.py:50` e `mcp_server/server.py:38` montam URLs a partir de
  `API_BASE_URL` e chamam a API **sem header de autenticação**. No instante em que a
  auth entrar, os dois tomam **401** — e o agente roda **no worker Celery, fora do
  request do usuário**, então precisa carregar o token de quem disparou o job (ou uma
  credencial de serviço + `dono` explícito no payload da task). É decisão de desenho:
  resolver junto com o mixin no PR1, não descobrir no PR2.
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

- ✅ **Dados de domínio no beta:** decidido — **Postgres local por testador** (ver
  "Arquitetura do beta").
- ✅ **Ordem 0A vs 0B:** decidido (24/07/2026) — **0B primeiro** (custo de atraso do
  `dono`), quebrada em 3 PRs; 0A encaixa entre PRs.
- **Identidade do agente/MCP nas chamadas HTTP** (0B.9): token do usuário propagado
  pela task Celery **vs** credencial de serviço. Decidir no plano do PR1.
- **Unificar `AGENTE_PROVIDER` e `LLM_PROVIDER`** num só env (0A.1) ou manter separados.
- **IA local vs API** — aguarda dado da Fase 0.
- **Regras de negócio a mudar** — aguarda dogfooding (Fase 1).
- **Hospedar (Fork A) vs desktop nativo (Fork B)** pros leigos — decidir após o beta.
- **Provider comercial** específico e **modelo local** final (3b vs 7b).
- **Hospedagem** (Fly/Railway/Render) — decidir perto da Fase 5.
