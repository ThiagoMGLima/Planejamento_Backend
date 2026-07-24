# Contexto — Fase 0B / PR0: views finas + agente em processo

**Status:** ✅ concluído em 24/07/2026
**Branch:** `claude/0b-pr0-views-finas-agente-em-processo` (commit `6465fa5`)
**Empilhada sobre:** `claude/docs-roadmap-metodo-trabalho` (`98f9675`, `e443f8c`)
**Nenhuma das três está na `main` ainda.**
**Item do ROADMAP:** 0B.9
**Próxima task:** 0B PR1 — ver `fase0b-pr1-dono.md` (tem 4 dúvidas abertas)

---

## 1. O que tinha que ser feito

Retirar a regra de negócio de dentro das views e fazer as ferramentas do agente
chamarem os services **em processo**, em vez de HTTP contra a própria API.

Não era uma tarefa planejada no ROADMAP original — nasceu da análise do PR1.

## 2. Por que (a origem)

A análise do PR1 (`fase0b-pr1-dono.md`) encontrou 32 pontos de query sem escopo,
4 vazamentos concretos, e este achado em `agente.py`:

```python
url = f"{settings.API_BASE_URL.rstrip('/')}{caminho}"
resp = requests.request(metodo, url, json=corpo, params=params, timeout=60)
```

**Código Django fazendo HTTP para o próprio Django**, de dentro do worker Celery.
Quando a autenticação da Fase 0B entrar, isso toma 401. As saídas seriam:

- propagar o JWT do usuário pela fila do Celery — mas o Redis do compose não tem
  senha e persiste em disco;
- renovar o token quando expirar — mas renovar exige o *refresh token*, que emite
  access tokens indefinidamente: guardar **ele** na fila é pior;
- credencial de serviço — funciona, mas cria um caminho privilegiado.

Chamando os services direto, **a pergunta deixa de existir**: o `dono_id` já vem no
payload da task. Daí o PR0 virar pré-requisito do PR1, e não parte dele — misturar a
refatoração com a migration de `dono` daria um diff irrevisável.

## 3. O que foi feito

### Services novos

| Arquivo | Conteúdo | Veio de |
| --- | --- | --- |
| `services/tarefas.py` | `promover`, `planejar`, `criar`, `resolver_classe` | `views.py` (TarefaViewSet) |
| `services/agenda.py` | `eventos_na_janela`, `pendentes`, `validar_janela`, `inicio_efetivo` | `views.py` (EventoViewSet.list, view `pendentes`) |

`promover` e `planejar` faziam a **mesma** checagem de classe palavra por palavra —
virou `resolver_classe`.

### Decisão de desenho: services devolvem domínio, não DTO

`agenda.eventos_na_janela` devolve `list[ItemAgenda(evento, ocorrencia)]`, não JSON.

Motivo: se o service devolvesse DTO, `services/` teria de importar `serializers`
(camada de apresentação), e o agente voltaria a consumir formato de apresentação —
só que sem a rede no meio, o que seria o mesmo acoplamento com outra roupa. Com
domínio, a view monta o JSON do contrato e o agente monta o resumo digerido, cada um
a partir da mesma fonte.

### Agente

`_api` foi removido. As 6 ferramentas (`listar_classes`, `criar_tarefa`,
`listar_pendentes`, `consultar_agenda`, `simular_plano`, `replanejar`) chamam services.

**Contrato para o modelo preservado:** erro continua virando `{"erro", "detalhe"}` em
vez de levantar, e o erro acionável de `classe_id` (com `classes_disponiveis` + dica)
segue idêntico — é o que faz o 7B se corrigir no turno seguinte em vez de desistir.

Duas validações **mudaram de dono sem mudar de comportamento**: `deadline` não-ISO e
janela acima do teto de ~92 dias agora são recusadas pela ferramenta, com a mesma
mensagem que a API dava. Antes o valor cru ia para a API e voltava 400.

### Limpeza

- `settings.API_BASE_URL` **removido** — ficou morto. Só o `mcp_server/` usa a
  variável, lida do ambiente (o compose a define no serviço `mcp`).
- Em `conversar`, o guarda `if isinstance(classes, list)` ("API fora do ar ⇒ segue sem
  classes") virou código morto: em processo, falha ali é o banco fora, e não há
  degradação útil — a task cai e o endpoint já responde `ia_indisponivel`.

### O MCP **não** mudou

Continua HTTP de propósito: é container separado servindo clientes externos, então a
fronteira é legítima. É a **única** que sobra — e é ali que entra a credencial de
serviço no PR1, concentrada em vez de espalhada.

## 4. Bugs e tropeços

| # | O que apareceu | Causa | Correção |
| --- | --- | --- | --- |
| 1 | 17 testes de `test_agente.py` quebraram | Mockavam `agente._api`, que deixou de existir | Reescritos para rodar as ferramentas de verdade contra o banco (ver §5) |
| 2 | `IntegrityError: duplicate key ... planner_classe_nome_key` | **As 5 classes padrão já vêm da migration `0002` no banco de teste** — `ClasseFactory(nome="Estudar")` colide com `Classe.nome unique` | Helper `classe(nome)` que faz `Classe.objects.get(nome=...)` em vez de criar |
| 3 | `assert 4 == 3` na expansão de recorrente | Erro meu de contagem: segundas entre 06 e 28/07 são 4 (06, 13, 20, 27) | Asserção trocada por lista explícita de datas — mais legível que um número |
| 4 | `'2026-07-08T20:00:00+00:00' != '2026-07-08T17:00:00-03:00'` | Erro meu: o banco guarda em UTC; comparei grafias, não instantes | `timezone.localtime(gravado).isoformat()` |
| 5 | Helper `ValidationError_uuid()` chamado dentro do `except` | Gambiarra minha para importar tarde | Import normal no topo (`DjangoValidationError`) |
| 6 | `timedelta` virou import não usado em `views.py` | Sobra da lógica que desceu | Removido (achado pelo ruff) |

**Achado de comportamento que vale lembrar:** `Classe.objects.get(pk=x)` levanta
`django.core.exceptions.ValidationError` — **não** `DoesNotExist` — quando `x` não é
sequer um UUID. O 7B às vezes manda o *nome* da classe no lugar do id, então os dois
casos precisam cair no mesmo erro (`ClasseDesconhecida`). Há teste para isso.

## 5. Testes

**231 passando**; `ruff`, `black --check` e `makemigrations --check` limpos.

O ganho real foi em `test_agente.py`. Ele stubava `agente._api`, o que verificava a
digestão do payload mas **não** que a ferramenta e a API concordassem — o fake podia
divergir do real indefinidamente. Agora as ferramentas rodam contra o banco de teste
(o cérebro LLM segue mockado por `FakeProvider`), e dá para afirmar coisas que antes
não dava:

```python
assert Tarefa.objects.filter(titulo="Física 2").exists()
```

Mais `test_services_tarefas_agenda.py`, novo, com 17 testes dos services extraídos.

`test_api.py` não foi tocado de propósito: serve de gabarito de que o comportamento
externo não mudou.

## 6. O que NÃO entrou

- Nada de `Perfil`, `dono` ou migration — é o PR1.
- Nada de autenticação — é o PR2.
- O MCP segue sem credencial (ganha no PR1).
- A abstração `LLMProvider` da 0A.1 segue pendente; `cenarios.py:199` e
  `cenarios.py:278` ainda instanciam `ollama.Client` direto.

## 7. Estado para quem pegar a próxima task

- Suíte verde, working tree limpo, 3 commits à frente da `main`.
- **PR1 está bloqueado pelas Q3–Q6** da seção 5 de `fase0b-pr1-dono.md` (identidade do
  `Perfil` antes do Supabase, destino dos dados de dev, `FeriadoLocal` por-dono vs
  catálogo, escopo do admin). Todas têm sugestão; falta o aval do usuário.
- O passo 2 do PR1 (manager que exige escopo) foi deliberadamente posto **antes** de
  mexer em services e views: com ele no lugar, a suíte aponta sozinha cada ponto que
  falta escopar, em vez de ser uma caçada arquivo por arquivo.
