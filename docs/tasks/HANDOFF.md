# Handoff — estado real em 08/08/2026

> **Leia isto antes de tocar em qualquer coisa.** O `CLAUDE.md` descreve o projeto; este
> arquivo descreve **o que está fora dele**: configuração local desta máquina, recursos
> externos já provisionados e trabalho em branches ainda não mergeadas. Nada aqui é
> dedutível do código.
>
> Ordem de leitura para um agente sem contexto: este arquivo → `CLAUDE.md` ("Estado
> atual") → `docs/tasks/README.md` (task ativa) → `ROADMAP.md`.

## 1. Onde está o trabalho recente

A 0A.1 e a 0A.3 **estão commitadas**, em duas branches empilhadas — ainda **não
mergeadas no `main`**. Confirme com `git log --oneline main..HEAD` antes de supor que o
`main` já tem `services/llm.py` ou `services/telemetria.py`.

| Branch | Base | Conteúdo |
| --- | --- | --- |
| `claude/0a1-llmprovider` | `main` (`b660ef9`) | **0A.1** — `services/llm.py`, providers Ollama/Anthropic/Mock por `LLM_PROVIDER` |
| `claude/0a3-instrumentacao` | `claude/0a1-llmprovider` | **0A.3** — `services/telemetria.py` + a instrumentação das 4 famílias, e um commit de docs de estado (este arquivo, o plano do Supabase, a 0A.6) |

**As duas são empilhadas de propósito:** a 0A.3 instrumenta `llm.gerar_json`, então não
compila sem a 0A.1. Mergear na ordem — 0A.1 primeiro.

As duas tasks foram desenvolvidas juntas, numa árvore só, e **separadas depois**. O
estado intermediário (0A.1 sem a 0A.3) foi verificado de verdade antes de virar commit:
285 testes, `ruff`, `black --check` e `makemigrations --check`. O topo da pilha fecha em
**296 testes**, com a mesma verificação.

O **frontend** (`../../Frontend/Planejamento_Frontend/`) tem mudanças sem commit, de
trabalho anterior não relacionado a estas tasks.

## 2. Configuração local desta máquina

Nada disto está versionado. Um agente que assumir aqui precisa saber.

### Portas — bloco 842x

O `docker-compose.override.yml` (gitignorado, presente) remapeia as portas porque **8000 e
8765 são disputadas por outros projetos desta máquina** (o backend do ProjMed ocupa a
8000 com `manage.py runserver`).

| Serviço | Padrão do repo | Nesta máquina |
| --- | --- | --- |
| API | 8000 | **8420** |
| MCP | 8765 | **8421** |
| Frontend (Vite) | 5173 | 5173 (inalterado) |

Dentro da rede do compose nada muda (`web:8000`, `mcp:8765`). O `.env` do frontend aponta
para `http://localhost:8420/api/v1`. Cliente MCP:
`claude mcp add --transport http planejador http://localhost:8421/mcp`.

O mesmo override também troca o Ollama para a **imagem de CPU**: esta máquina é Intel
Iris Xe, não tem `/dev/kfd`, e o `docker-compose.yml` versionado assume a RX 7600 via
ROCm. Sem o override a stack inteira trava, porque o `web` depende do `ollama`.

### Modelo de IA

`OLLAMA_MODEL=qwen2.5:3b-instruct` (não o 7b do default), em CPU. Medido em 08/08/2026
com o modelo quente: **~15–17 tok/s**. Essa é a linha de base da 0A.5 e da 0A.6.

### Dados

**O banco foi zerado a pedido do usuário em 08/08/2026.** Foram apagados 23 tarefas,
28 eventos, 3 ocorrências, 8 regras de recorrência e 15 registros de execução.

Mantidos de propósito: as **5 classes padrão**, o **feriado de Curitiba** e o **perfil
local**. Não são dados de demonstração — `Evento.classe` é FK obrigatória, e sem classe
nenhuma o app não permite criar nada.

Seeds (`seed_demo`, `seed_planejamento`) **só rodam por comando**; nada os dispara no
boot. O que roda em banco novo são as migrations `0002` (classes) e `0006` (feriado), uma
vez só. **O usuário está usando o sistema de verdade a partir de agora** — não rode seed
sem pedir.

## 3. Supabase — já provisionado

Pré-requisito externo do PR2, **cumprido e verificado ponta a ponta**. Detalhe em
[`fase0b-pr2-supabase.md`](fase0b-pr2-supabase.md).

| | |
| --- | --- |
| Project ref | `wrrusgngsslijdyvutgb` |
| URL | `https://wrrusgngsslijdyvutgb.supabase.co` (**`.co`**, não `.com`) |
| Algoritmo | **ES256** (EC P-256) — sistema de Signing Keys, nada a migrar |
| `iss` | `https://wrrusgngsslijdyvutgb.supabase.co/auth/v1` |
| `aud` | `authenticated` |
| Site/Redirect URL | `http://localhost:5173` |
| Providers | email ligado; **Google adiado** (decisão D2) |
| Confirm email | **desligado** |

Verificação já feita, de dentro do container `web`: `PyJWT[crypto]` + `PyJWKClient` contra
o JWKS remoto, assinatura válida, `iss` e `aud` conferidos. **A dependência e o desenho do
PR2 estão validados antes de existir código.**

Duas armadilhas achadas e registradas no plano:

- **`email_verified` vem `true` sem verificação nenhuma** (confirmação desligada). O PR2
  não pode tratar esse claim como prova de identidade. A chave é o `sub`.
- **Ligar o Google exige religar o Confirm email antes.** O Supabase linka identidades
  pelo email; com confirmação desligada isso vira takeover por pré-registro. As duas
  configurações ficam em telas diferentes e **não são independentes**.

Usuário de teste criado: `sub` `467dee6c-698f-485f-94d1-2068b3922d14`
(`…+planejador-teste@gmail.com`).

## 4. Onde o trabalho parou

**0A.3 fechou os passos 1–8 do ciclo**, e o passo 9 (PR) está em curso — sem gate de
frontend, porque não houve mudança de contrato HTTP (decisão Q9). O prompt de sincronia
do passo 8 é "nada a fazer no frontend": as mudanças de assinatura foram todas internas a
`services/`, sem endpoint, campo ou comportamento novo.

Duas frentes disponíveis, sem dependência entre si:

- **0B / PR2** — a espinha. Pré-requisito externo cumprido; falta o plano de
  implementação e o código. Ver a seção "Consequências no backend" do plano do Supabase,
  que já lista dependência (`PyJWT[crypto]`), cache de JWKS com refetch por `kid`
  desconhecido, claims a validar e a credencial de serviço do MCP herdada do PR1.
- **0A.5** — matriz 3b × 7b. Destravada pela 0A.3 e **melhora quanto mais tempo passar**:
  a telemetria acumula a cada uso real. É o único item que rende esperando.

**Trade-off que o usuário conhece e ainda não decidiu:** o PR2 põe o uso pessoal dele
atrás de um login que depende de internet. Ele começou a usar o sistema para se planejar
de verdade. Adiar o PR2 em favor da 0A.2/0A.5 é escolha legítima — está registrado nos
riscos do plano do Supabase.

## 5. Coisas que já custaram investigação

Não repita:

- **`LOGGING` só é aplicado por `django.setup()`.** Testar log com
  `docker compose exec web python -c` não funciona. Use `manage.py shell -c`.
- **A suíte não pode escrever no JSONL de telemetria real.** Já aconteceu: 11 registros
  sintéticos entraram no arquivo do usuário. Há fixture autouse em `conftest.py`
  desligando; se você adicionar teste que exercite `llm.gerar_json`, não a contorne.
- **`.telemetria/llm.jsonl` é escrito como root** pelo container. Apagar com
  `docker compose exec web rm -rf /app/.telemetria`.
- **`!reset` em override de compose** é obrigatório para *substituir* lista em vez de
  concatenar. Vale para `devices`, `group_add` e `ports`. Para remover um serviço inteiro:
  `ollama: !reset null` — testado, funciona, e exige soltar o `depends_on` de quem
  dependia dele.
