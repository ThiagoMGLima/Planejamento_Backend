# Fase 0B / PR2 — Provisionar o Supabase (pré-requisito externo)

> **Plano.** Cobre o que **destrava** o PR2: criar e configurar o projeto Supabase.
> É a parte que o `docs/tasks/README.md` marca como "do usuário".
> O plano de implementação do código Django vem **depois** — duas das decisões abaixo
> (D1 e D2) determinam o que se escreve, então planejá-lo agora seria planejar sobre
> premissa. Data: 08/08/2026. Verificado contra os docs do Supabase nesta data.

## O que o PR2 precisa de verdade

Menos do que parece. O backend **só valida JWT** — não cria usuário, não chama a API
admin, não guarda sessão. Consequência prática: **nenhum segredo do Supabase precisa
chegar ao Django.** A verificação é local, contra as chaves públicas do JWKS.

```
Frontend (supabase-js)  --login-->  Supabase Auth  --JWT-->  Frontend
Frontend  --Authorization: Bearer <JWT>-->  Django
Django  --valida com o JWKS (público, cacheado)-->  perfil_do_request()
```

`services/perfis.perfil_do_request()` é a **única** função que o PR2 troca — o PR1
deixou o resto do backend já multi-tenant.

---

## Decisões antes de clicar

### D1 — Modelo de chave de assinatura

O Supabase tem dois sistemas:

| | Legacy | Signing Keys (atual) |
| --- | --- | --- |
| Algoritmo | HS256, **segredo compartilhado** | ES256 (P-256) ou RS256; HS256 ainda possível |
| Como o Django verifica | precisa do segredo **no `.env` de cada testador**, ou bate em `/auth/v1/user` a cada request | busca a chave **pública** no JWKS e verifica local |
| Rotação | manual, quebra tudo | chave standby + rotação sem downtime |
| Postura | "no longer recommended" | recomendado |

**Recomendação: Signing Keys com ES256.** O argumento decisivo é o princípio 9: com
HS256, a chave que **verifica** é a mesma que **assina**. Pôr esse segredo no `.env` de
cada amigo testador significa que qualquer um deles pode forjar um token de qualquer
usuário. Isso não é isolamento, é confiança. Com ES256, o `.env` do testador não tem nada
que permita forjar nada — só a URL pública do JWKS.

ES256 sobre RS256: assinatura menor e verificação mais rápida; ambos são suportados.

> Se o painel criar o projeto já no modo legacy, existe migração: **Migrate JWT secret**
> → cria chave assimétrica standby → **Rotate keys** → esperar o TTL dos tokens (1h + 15min)
> → revogar o segredo legado. Dá para fazer antes de existir qualquer usuário, que é agora.

### D2 — Quais providers entram no PR2

O 0B.1 pede "Google + email". **Recomendação: email agora, Google depois.**

Google exige um projeto no Google Cloud, consent screen, OAuth client, escopos e branding
— trabalho que não é do Django e não muda uma linha do PR2. O email destrava tudo que o
PR2 precisa testar: login, JWT válido, provisionamento JIT do `Perfil`, e o 0B.7
(múltiplas contas na mesma máquina para provar isolamento). Google vira configuração de
painel depois, sem PR.

Se discordar, a Parte D abaixo tem o passo a passo.

### D3 — Um projeto ou um por testador

O ROADMAP já decidiu: **"projeto compartilhado, na nuvem"**. Um só. Cada testador roda o
próprio Postgres local; o que é compartilhado é só a identidade. Nada a decidir, fica
registrado para não ser reaberto.

---

## Passo a passo

### Parte A — Projeto

Na tela **Create a new project**, campo a campo:

| Campo | Valor | Por quê |
| --- | --- | --- |
| **GitHub (optional)** | **deixar em branco** | Essa integração faz o Supabase **deployar schema** do repo (`supabase/migrations`) para o Postgres dele. Aqui o schema é do Django e mora no Postgres local — é a decisão de "Arquitetura do beta". Conectar dá a falsa impressão de que o Supabase gerencia algo que ele não gerencia. |
| **Project name** | `planejador-auth` | O nome é o único lugar que registra que este projeto é **só identidade**. Sem isso, daqui a seis meses alguém assume que o banco está em uso. |
| **Database password** | gerar e guardar | Não se usa agora (o banco só entra na Fase 3.1) e não é recuperável. |
| **Region** | São Paulo, se disponível | Só afeta latência do login; o resto roda local. |
| **Enable Data API** | ☐ **desmarcar** | Liga o PostgREST (`/rest/v1`) sobre o schema público. O projeto inteiro nunca consome isso — nem na Fase 3.1, quando o Django conectaria direto por `DATABASE_URL`. Superfície de ataque sem contrapartida. **Não afeta o Auth**: o GoTrue (`/auth/v1`) é serviço separado. |
| **Automatically expose new tables** | ☐ **desmarcar** | O próprio Supabase recomenda. Armadilha futura concreta: na Fase 3.1, com isso ligado, `planner_perfil`/`planner_evento` e todas as tabelas do Django ficariam expostas via PostgREST a quem tiver a chave publishable — que é pública por definição. |
| **Enable automatic RLS** | ☐ deixar como está | Com a Data API desligada não há caminho PostgREST a proteger, e ligar agora produziria, na Fase 3.1, tabelas com RLS **sem política** — que devolvem zero linhas em silêncio. A análise de RLS está em "Arquitetura do beta"; é decisão para quando o banco mudar de lugar. |

Depois de criado:

1. Anote o **project ref** (o `<ref>` de `https://<ref>.supabase.co`).
2. Settings → JWT Keys: confirme que está no sistema de **Signing Keys** com **ES256**
   (D1). Se estiver legacy, migre agora, antes de existir usuário.

### Parte B — URLs

Authentication → URL Configuration:

- **Site URL**: `http://localhost:5173`
- **Redirect URLs**: `http://localhost:5173`

Sem isso o login autentica e não volta para o app.

> **A porta é 5173** — verificado em 08/08/2026: `vite.config.js` fixa `server.port: 5173`
> e o `CORS_ALLOWED_ORIGINS` do backend já a inclui. O bloco de portas **842x** desta
> máquina cobre só API (8420) e MCP (8421), que moram no `docker-compose.override.yml`
> local; o frontend segue no default do Vite.
>
> **Se um dia o Vite mudar de porta**, quatro lugares precisam mudar **juntos**, e o
> quarto é remoto e fácil de esquecer:
> `vite.config.js` · `README.md` do frontend · `CORS_ALLOWED_ORIGINS` do backend ·
> **Redirect URLs aqui no painel do Supabase**. Esqueça o último e o login autentica e
> não volta — sintoma que não aponta para a causa.
>
> Não pré-registre portas "por via das dúvidas": a lista de Redirect URLs é fronteira de
> segurança do OAuth, não conveniência. Registre a porta que existe.

### Parte C — Provider de email

Authentication → Providers → Email: ligado.

> **Atenção — "Confirm email" vem ligado por padrão.** Com ele ligado, `signup` **não**
> devolve sessão até o usuário clicar no link, e o teste da Parte E falha sem motivo
> aparente. Para o beta técnico, sugiro **desligar** enquanto testamos e religar antes de
> abrir para os amigos. Decisão sua; só não descubra isso depurando o Django.

### Parte D — Google *(adiável, ver D2)*

**O que ganha:** um botão "entrar com Google" em vez de email+senha. O token devolvido é
idêntico (mesmo `iss`, `aud`, `sub`, ES256); só muda `app_metadata.provider` para
`"google"`. **Zero linhas no Django** — o `SupabaseJWTAuthentication` não pergunta como o
usuário entrou. É configuração de painel, não código, e por isso não está no caminho
crítico do PR2. O valor real aparece na **Fase 5** (princípio 4: *"login Google — trivial
pra leigo"*), não no beta técnico.

> 🔒 **Pré-requisito não-óbvio: religar "Confirm email" antes de ligar o Google.**
>
> O Supabase faz **identity linking automático** — mesmo email ⇒ a identidade nova é
> anexada ao usuário existente, com o **mesmo `sub`**. Isso é bom para nós (trocar de
> provider não cria um segundo `Perfil`; valida chavear por `sub`, decisão Q3 do PR1).
>
> Mas a condição para linkar é **email verificado**, e a doc diz o porquê: *"will not link
> to accounts with unverified emails to prevent account takeover attacks"*. Com
> confirmação desligada, o Supabase auto-confirma no signup e a proteção some. O ataque:
> alguém se cadastra com o email de um amigo (auto-confirmado, sem prova de posse), o
> amigo entra depois com Google, o Supabase linka as identidades, e o invasor fica com a
> conta — mesmo `sub`, mesmo `Perfil`.
>
> As duas configurações vivem em telas diferentes do painel e **não são independentes**.
> Só email/senha e um usuário só: desligado é prático e inofensivo. Google ligado ou
> amigos entrando: confirmação **tem** que voltar.

No Google Cloud:

1. Projeto + Google Auth Platform → Audience, e escopos `openid`,
   `.../auth/userinfo.email`, `.../auth/userinfo.profile`.
2. Clients → **OAuth client ID** → tipo **Web application**.
3. **Authorized JavaScript origins**: `http://localhost:5173`
4. **Authorized redirect URIs**: `https://<ref>.supabase.co/auth/v1/callback`
5. Copie Client ID e Client Secret.

No Supabase: Authentication → Providers → Google → colar os dois.

### Parte E — ✅ EXECUTADA em 08/08/2026

Resultado real do projeto provisionado — estes são os valores que o PR2 usa:

| | |
| --- | --- |
| Project ref | `wrrusgngsslijdyvutgb` |
| URL | `https://wrrusgngsslijdyvutgb.supabase.co` — **`.supabase.co`**, não `.com` |
| Algoritmo | **ES256** (EC P-256), `use: sig`, `kid` `2de0ae7a-aa62-44b1-be96-b20c9b80bcd8` |
| `iss` | `https://wrrusgngsslijdyvutgb.supabase.co/auth/v1` |
| `aud` | `authenticated` |
| Confirm email | **desligado** |
| Chave publishable | vive no `.env` do frontend (não versionado). Não é copiada para cá. |

**Verificação de assinatura feita ponta a ponta**, de dentro do container `web`, com
`PyJWT[crypto]` + `PyJWKClient` contra o JWKS remoto: assinatura válida, `iss` e `aud`
conferidos. É exatamente o caminho que o `SupabaseJWTAuthentication` vai percorrer —
então a dependência e o desenho estão validados antes de existir código.

Claims disponíveis no token: `iss`, `sub`, `aud`, `exp`, `iat`, `email`, `phone`, `role`,
`session_id`, `aal`, `amr`, `is_anonymous`, `app_metadata` (com `provider`), `user_metadata`.

> ⚠️ **`email_verified` vem `true` sem verificação nenhuma.** Com "Confirm email"
> desligado, o Supabase marca o email como verificado no ato do signup — ninguém provou
> posse daquele endereço. O PR2 **não pode** tratar esse claim como prova de identidade
> (nem usar email como chave de nada). A chave é o `sub`, que é o que já vai para
> `Perfil.supabase_id`. Religar a confirmação antes de abrir aos amigos é decisão à parte.

> 🧹 **Usuário de teste a limpar:** `sub` `467dee6c-698f-485f-94d1-2068b3922d14`
> (`…+planejador-teste@gmail.com`). Apagar em Authentication → Users quando não for mais
> útil — ou manter, que serve de conta de teste para o PR2.

<details>
<summary>Comandos usados (para reproduzir num projeto novo)</summary>

Com a chave publishable (`sb_publishable_...`, ou a `anon` se o projeto for legacy):

```bash
REF=<seu-ref>
PUB=<sb_publishable_...>

# 1. as chaves públicas existem e são ES256
curl -s "https://$REF.supabase.co/auth/v1/.well-known/jwks.json" | jq '.keys[] | {kty,alg,kid}'

# 2. criar um usuário e pegar um token
curl -s -X POST "https://$REF.supabase.co/auth/v1/signup" \
  -H "apikey: $PUB" -H "content-type: application/json" \
  -d '{"email":"eu@exemplo.com","password":"uma-senha-boa"}' | jq -r .access_token
```

Se o segundo comando devolver `null`, é o "Confirm email" da Parte C.

Decodifique o payload do token (sem verificar — só para ver os claims):

```bash
TOKEN=<cole aqui>
python3 -c "import sys,json,base64;p=sys.argv[1].split('.')[1];print(json.dumps(json.loads(base64.urlsafe_b64decode(p+'='*(-len(p)%4))),indent=2))" "$TOKEN"
```

Esperado: `iss` = `https://<ref>.supabase.co/auth/v1`, mais `sub` (o UUID que vai para
`Perfil.supabase_id`), `email`, `role`, `aud`, `exp`.

Prova final — verificar a assinatura como o Django vai verificar:

```bash
docker compose run --rm -T -e TOKEN="$TOKEN" web sh -c \
  "pip install -q 'PyJWT[crypto]' && python -c \"
import os, jwt
from jwt import PyJWKClient
ISS='https://$REF.supabase.co/auth/v1'
tok=os.environ['TOKEN']
key=PyJWKClient(f'{ISS}/.well-known/jwks.json').get_signing_key_from_jwt(tok)
print(jwt.decode(tok, key.key, algorithms=['ES256'], audience='authenticated', issuer=ISS))
\""
```

</details>

---

## O que me entregar

| Item | Sigiloso? | Para quê |
| --- | :---: | --- |
| Project ref / URL | não | montar `iss` e a URL do JWKS |
| Confirmação do algoritmo (ES256/RS256/HS256) | não | define o código de verificação |
| Chave **publishable** (`sb_publishable_…` ou `anon`) | não (é de navegador) | vai no `.env` do **frontend** |
| Um access token de teste | sim, mas expira em 1h | teste de ponta a ponta |

**Não me mande** — e não ponha em `.env` nenhum:

- `sb_secret_…` / `service_role` — ignora RLS, acesso total ao projeto. O Django não tem
  o que fazer com ela.
- O **JWT Secret** legado, se você acabar ficando em HS256 — é a chave que assina.
- A senha do banco do Supabase.

Se o projeto ficar em HS256 (contra a recomendação D1), me avise: muda o código **e**
muda a postura de segurança do beta, e isso precisa ser registrado como desvio.

---

## Consequências no backend *(esboço — não é o plano do PR2)*

Fica aqui só o que as decisões acima já determinam:

- **Dependência nova**: uma lib de JWT com suporte a JWKS (`PyJWT[crypto]` + `PyJWKClient`
  é o candidato óbvio). Vai para `requirements.txt`.
- **Cache do JWKS**: o edge do Supabase cacheia 10 minutos. O cliente precisa cachear
  também e **refazer o fetch quando aparecer um `kid` desconhecido** — senão a primeira
  rotação de chave derruba todo mundo até o processo reiniciar.
- **Claims a validar**: assinatura, `exp`, `iss` e `aud`. Validar `iss` não é
  formalidade: sem isso, um token de outro projeto Supabase qualquer passaria.
- **Provisionamento JIT**: `sub` → `Perfil.supabase_id`. O PR1 já deixou a coluna
  separada da PK exatamente para isto (decisão Q3). O perfil local existente **vira** a
  conta no primeiro login (decisão Q4 do PR1) — é assim que os dados que você criar
  daqui até lá não se perdem.
- **`DEFAULT_PERMISSION_CLASSES` inverte** para `IsAuthenticated`.
- **Credencial de serviço do MCP** — herdada do PR1. O `mcp` é container separado
  falando HTTP com o `web`; quando o `web` exigir auth, ele toma 401. Entra neste PR.
- **Envs novas**: `SUPABASE_URL` / `SUPABASE_JWKS_URL`, `SUPABASE_JWT_AUD`. Nada secreto.

---

## Riscos e pontos de atenção

**O app deixa de funcionar offline.** Hoje você abre o Planejador sem internet. Depois do
PR2, o login passa por um serviço na nuvem. O princípio 3 do ROADMAP já aceita isso
("Não-hospedado + Supabase Auth convivem"), mas vale dizer em voz alta agora que você
começou a usar o sistema todo dia: **o PR2 adiciona atrito ao seu uso pessoal** em troca
de destravar o beta com os amigos. Se o dogfooding for a prioridade das próximas semanas,
adiar o PR2 é uma escolha legítima — o ROADMAP tem 0A.2/0A.4/0A.5 na fila.

**Free tier pausa projeto inativo.** Projetos gratuitos do Supabase hibernam após um
período sem uso, e um testador que voltar depois de semanas não consegue entrar até o
projeto religar. Confirme a política atual no painel; se for problema, é argumento para
plano pago ou para o beta ter janela definida.

**Rotação de chave é um teste, não um detalhe.** O sistema de signing keys existe para
rotacionar. Vale exercitar uma rotação no ambiente de teste antes de distribuir aos
amigos — é o cenário que quebra em produção seis meses depois, quando ninguém lembra.

**Nada disso mexe nos dados locais.** Provisionar o Supabase não toca no Postgres do
compose. Você pode fazer a Parte A–E hoje e o PR2 semana que vem, sem perder nada.
