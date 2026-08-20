# Handoff — estado real em 19/08/2026

> **Leia isto antes de tocar em qualquer coisa.** O `CLAUDE.md` descreve o projeto; este
> arquivo descreve **o que está fora dele**: configuração local desta máquina, recursos
> externos já provisionados e trabalho em branches ainda não mergeadas. Nada aqui é
> dedutível do código.
>
> Ordem de leitura para um agente sem contexto: este arquivo → `CLAUDE.md` ("Estado
> atual") → `docs/tasks/README.md` (task ativa) → `ROADMAP.md`.

## 1. Onde está o trabalho recente

**Os PRs #24 e #25 (0A.1 e 0A.3) foram mergeados** — `origin/main` está em `82d6537`,
que é o merge do #25. O texto anterior desta seção dizia que estavam pendentes; não
estão mais.

As **seis tasks acumuladas viraram o PR #26** em 19/08/2026, assumido como PR de
acumulado (1.1 A/B/C/C2 e 1.2 A/B). **Daqui em diante volta a valer 1 task = 1 PR**, em
branch própria.

| | |
| --- | --- |
| Branch | `claude/fase1-pra-parametros-tarefa` → `origin` |
| PR | **#26**, aberto contra `main` |
| Commits à frente de `origin/main` | 17 |

O nome da branch descreve só a primeira das seis tasks — é resquício de quando ela
acumulou. Não repita: **branch nova por task**.

Também segue não-mergeada a branch remota `claude/0b-perfil-dono-e-escopo`. O **código**
do PR1 chegou ao `main` por outro caminho, mas **um commit ficou de fora**: `e3f8a45`,
só de docs, que carregava dois itens de backlog. Ambos foram recuperados em 19/08/2026 e
estão agora no `ROADMAP.md` — o do comparador de cenários (`MAX_CENARIOS` conta a base)
em "Backlog anotado", e o aviso de que **o PR2 tem metade de frontend** no item 0B.2.
Não há mais nada a resgatar dali.

O **frontend** (`../../frontend/Planejamento_Frontend/`, branch `main`) tem
`package.json` e `package-lock.json` modificados sem commit, de trabalho anterior não
relacionado a estas tasks.

## 2. Configuração local desta máquina

Nada disto está versionado. Um agente que assumir aqui precisa saber.

> ⚠️ **O projeto mudou de máquina.** Até 08/08/2026 este arquivo descrevia um notebook
> Intel Iris Xe, com `docker-compose.override.yml` remapeando portas para o bloco 842x
> e rodando `qwen2.5:3b-instruct` em CPU. **Nada disso vale aqui.** O trabalho de
> 14/08 em diante acontece no desktop descrito abaixo. Se você leu "portas 8420/8421"
> ou "modelo 3b" em algum contexto antigo, era a outra máquina.

### Esta máquina (desktop) — o compose versionado roda sem override

**Não existe `docker-compose.override.yml` aqui, e não é preciso.** A configuração
ROCm e as portas padrão do `docker-compose.yml` versionado funcionam como estão,
porque este desktop é exatamente a máquina para a qual ele foi escrito.

| | |
| --- | --- |
| GPU | Radeon **RX 7600** (gfx1102), `/dev/kfd` presente, grupo `render` gid 990 |
| Imagem do Ollama | `ollama/ollama:rocm` (a do compose versionado), `HSA_OVERRIDE_GFX_VERSION=11.0.0` |
| Modelo | `qwen2.5:7b-instruct` — o default do repo, **não** o 3b |
| API | **8000** (padrão) |
| MCP | **8765** (padrão) |
| Frontend (Vite) | 5173 |

Cliente MCP: `claude mcp add --transport http planejador http://localhost:8765/mcp`.

**Desempenho medido aqui:** ~46,5–46,8 tok/s por chamada do agente (telemetria da
0A.3, 15/08/2026), contra os ~15–17 tok/s do 3b em CPU na máquina antiga. **Teto de
VRAM: 8176 MiB** — o 7b Q4 (4,7 GB) cabe; um 14b Q4 (~9 GB) derrama para a CPU e perde
os 47 tok/s. Subir de modelo localmente exige trocar de placa.

**Consequência para a 0A.6** (IA remota na LAN): ela foi adiada em 08/08 "por falta de
hardware em mãos", e **metade dessa premissa caiu** — o desktop com a RX 7600 é esta
máquina. O que ainda falta é o host sempre-ligado (o Raspberry Pi) e, pelo princípio 9,
o PR2: servir a API na LAN sem autenticação nenhuma não é opção.

### Nome do projeto compose e o volume de dados — cuidado real

O projeto compose é `planejamento_backend`, derivado do nome do diretório. Existe uma
**cópia antiga e não-git** em `~/Documents/Projetos/planejamento/backend/Planejamento_Backend`
com o mesmo nome de diretório, logo o **mesmo projeto compose** — os volumes
`planejamento_backend_pgdata` (os dados reais do usuário) e
`planejamento_backend_ollama_models` são compartilhados entre as duas cópias. Foi o que
preservou os dados ao subir o clone novo.

- **Suba sempre a partir de `Projetos/Planner/backend/Planejamento_Backend`.**
- **Nunca rode `docker compose` a partir da cópia antiga**, e **nunca `down -v`.**
- Se o diretório for renomeado um dia, fixe `COMPOSE_PROJECT_NAME=planejamento_backend`
  para não órfãozar o volume.

### Dados — o usuário está usando o sistema de verdade

O banco foi zerado em 08/08/2026, e **desde então encheu de uso real**. Estado
conferido em **19/08/2026**:

| | |
| --- | --- |
| Perfis | 1 (o local) |
| Tarefas | **41** — 9 com `estrategia=TARDE`, **32 sem estratégia nenhuma** |
| Eventos | **166**, dos quais **92 são blocos de estudo já promovidos** |
| Regras de recorrência | 7 |
| Ocorrências | 1 |
| Classes | 6 — as 5 padrão + `Academia`, criada pelo usuário |

Composição dos eventos por classe: `Aula` 62 (58 **avulsos**, 4 recorrentes), `Trabalho`
47 (46 avulsos), `Estudar` 46 (todos avulsos, são blocos promovidos), `Prova` 9 (todos
avulsos), `Academia` 2 (recorrentes).

Os **58 avulsos de `Aula` + 9 de `Prova`** são exatamente o que a Fase 1.2 existe para
consertar: são as 3 disciplinas cujo PDF de planejamento de ensino foi lançado à mão,
data por data, em vez de série recorrente. **Não os apague à mão** — quem faz isso é o
`importar_planejamento_ensino --substituir` no PR C, e há um backup antes
(`backup-planejador-20260818-1825.sql`, na raiz de `Projetos/Planner/`).

Seeds (`seed_demo`, `seed_planejamento`) **só rodam por comando**; nada os dispara no
boot. **Não rode seed sem pedir** — não há mais banco vazio para semear.

**A grade do semestre 2026/2** (7 disciplinas da UTFPR, 18/08 a 17/12, sexta livre,
academia 3×/semana, reunião AVMMED quarta 19:15) é o dado por trás desses números. Os
PDFs de planejamento de ensino estão em `../../aulas/` (só ASL, EG1 e Redes; as outras
4 ainda vêm).

### Uma inconsistência inócua no `.env`

`AGENTE_PROVIDER=ollama`, mas `AGENTE_MODEL=claude-opus-4-8`. **Não quebra nada**:
`AGENTE_MODEL` só é lido pelo provider Anthropic (`agente.py`, `_chamar`), então com o
provider em `ollama` a variável fica inerte. Vale saber antes de gastar tempo achando
que o agente está falando com a Anthropic — não está.

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

**A Fase 1.2 / PR A fechou os passos 1–8 do ciclo** em 18/08/2026 (commit `d3e06a5`),
com árvore limpa e suíte verde. O passo 9 (prompt de sincronia) é "nada a fazer no
frontend", e com razão registrada: o PR não acrescentou campo nenhum ao payload —
`titulo`, `descricao` e `classe` já existiam e passam a vir com outro valor em algumas
ocorrências. O passo 10 (PR) não aconteceu, junto com os das quatro tasks anteriores
(ver §1).

**A próxima task é o PR B da 1.2** (`importar_planejamento_ensino`), cujo gate já foi
respondido. O detalhe do que fazer está em `docs/tasks/README.md`.

**A decisão que continua aberta é de ordem, não de conteúdo:** fechar a 1.2 (PR B → PR
C) ou voltar para o **PR2 da Fase 0B**. O PR2 **não está mais bloqueado** — o
pré-requisito externo foi cumprido em 08/08 (§3 abaixo) — e está parado por falta do
plano de implementação, não de terceiro. Os argumentos dos dois lados estão na seção
"Decisões em aberto" do `ROADMAP.md`.

**Trade-off que o usuário conhece e ainda não decidiu:** o PR2 põe o uso pessoal dele
atrás de um login que depende de internet, e ele está usando o sistema de verdade,
todo dia, com o semestre em curso. Adiar o PR2 é escolha legítima — está registrado nos
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
- **Modificar um queryset descarta o `prefetch_related` dele.** Achado no PR A da 1.2:
  `evento.ocorrencias.select_related(...)` dentro de `expandir` ignorava o cache de quem
  montou a query e virava 1 query por evento recorrente. A saída foi
  `recurrence.prefetch_ocorrencias()` — um `Prefetch` com o `select_related` dentro. **Vai
  chamar `expandir`? use o helper**; é o que mantém a janela em 4 queries, constante.
- **`!reset` em override de compose** é obrigatório para *substituir* lista em vez de
  concatenar. Vale para `devices`, `group_add` e `ports`. Para remover um serviço inteiro:
  `ollama: !reset null` — testado, funciona, e exige soltar o `depends_on` de quem
  dependia dele.
