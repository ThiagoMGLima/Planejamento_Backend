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

## Fase 0 — Beta técnico com contas (Docker, não-hospedado + Supabase Auth)  🔜  *(ATIVA)*

Objetivo: amigos técnicos rodando em **hardware variado** pra (a) feedback de produto/UX,
(b) decidir **IA local vs API** com dado real, e (c) **testar contas + segurança do Auth**.
Dois fluxos de trabalho em paralelo.

### 0A — Provider trocável + empacotamento
- **0A.1 Abstração `LLMProvider`** em `planejamento_ia.py`: `gerar_diretrizes(contexto)
  -> Diretrizes`, com `OllamaProvider` / `AnthropicProvider` / `OpenAIProvider` /
  `MockProvider`, por env (`LLM_PROVIDER=ollama|api|mock`). `validar_diretrizes`
  (guarda-corpo) segue independente do provider. **Default `ollama`** (nada muda pra
  quem roda local).
- **0A.2 Empacotamento local:** auto-pull do modelo no boot + **profiles do compose**
  (`--profile local` sobe Ollama; `--profile api` não sobe).
- **0A.3 Instrumentação:** logar tempo de parede real + (modo api) tokens.
- **0A.4 Launcher cross-platform:** `start.*`/`stop.*` (mac/linux/windows) + README de
  testador. Pré-requisito: Docker (aceitável pra técnico).
- **0A.5 Teste de tamanho de modelo:** incluir `qwen2.5:3b` na matriz.

### 0B — Contas + autenticação (fundação, puxada pra frente)
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
- **0B.6 Seed das 5 classes padrão** vira **por-usuário** (no Perfil, JIT) — não mais global.
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
- **IA local vs API** — aguarda dado da Fase 0.
- **Regras de negócio a mudar** — aguarda dogfooding (Fase 1).
- **Hospedar (Fork A) vs desktop nativo (Fork B)** pros leigos — decidir após o beta.
- **Provider comercial** específico e **modelo local** final (3b vs 7b).
- **Hospedagem** (Fly/Railway/Render) — decidir perto da Fase 5.
