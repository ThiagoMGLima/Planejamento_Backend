# Roteiro de teste humano — Fase 1.1 (PRs A, B, C)

> **Passo 7 do ciclo.** Só o que exige um humano. Tudo o que dá para provar com
> `pytest` ou chamada de service **já foi provado** — a lista do que já está
> coberto está no fim, para você não repetir.
>
> Para cada item: **o que fazer**, **o que observar**, **sinal de problema**.
> Onde houver escolha entre dois comportamentos aceitáveis, a decisão é sua.

## Antes de começar

```bash
cd ~/Documents/Projetos/Planner/backend/Planejamento_Backend
git branch --show-current      # deve ser claude/fase1-pra-parametros-tarefa
docker compose ps              # web healthy
```

Backup do banco antes de qualquer coisa que grave:

```bash
docker compose exec -T db pg_dump -U planejador planejador > ~/backup-antes-teste.sql
```

---

## 1. O plano de estudo é vivível? *(julgamento de produto)*

O solver prova que as sessões **cabem**; só você sabe se elas **servem**.

**O que fazer** — veja o plano proposto para as tarefas de estudo que já entram
no horizonte:

```bash
docker compose exec -T web python manage.py shell -c "
from django.utils import timezone
from planner.services.perfis import perfil_local
from planner.services import replanejamento as R
rp = R.replanejar(perfil_local(), agora=timezone.now())
for tid, i in rp.diff.items():
    for s in i['movidas']:
        print(f\"{s['para']['inicio'][:16].replace('T',' ')} -> {s['para']['fim'][11:16]}  {i['titulo'][:45]}\")
    for s in i['criadas']:
        print(f\"{s['inicio'][:16].replace('T',' ')} -> {s['fim'][11:16]}  {i['titulo'][:45]}\")
" | sort
```

**O que observar**

- **A véspera da prova.** O estudo termina *quando a prova começa*. Você quer
  isso, ou quer uma folga (chegar sem estudar em cima da hora)? Se quiser folga,
  é `nao_depois_de` na tarefa — diga qual folga e eu aplico.
- **O teto de 2h/dia por tarefa.** Está confortável, ou você prefere menos dias
  com mais horas? É a preferência `max_min_por_dia_por_tarefa`.
- **Sessões de 30 minutos.** O solver usa restos curtos para fechar a conta.
  Meia hora de estudo é útil para você, ou é ruído na agenda?
- **Fins de semana.** O relaxamento só libera sábado/domingo quando não cabe em
  dia útil. Confira se os que ele pegou fazem sentido (a P1 de física puxou
  domingo 16/08).

**Sinal de problema:** sessão em horário que você nunca usaria, dois estudos
diferentes empilhados no mesmo dia às vésperas de provas distintas, ou uma
matéria com todas as sessões num único dia.

---

## 2. As 5 provas de dezembro somem do plano — você concorda? *(decisão)*

**Este é o item mais importante do roteiro.** Achei durante o teste que o
`TARDE` estava reproduzindo o bug original: com horizonte de 92 dias (teto
15/11), o estudo da prova de **10/12** caía em **09/11** — um mês antes —, porque
a tarefa ancorava no fim do *horizonte* em vez de no *prazo*.

Corrigi: tarefa `TARDE` com prazo além do horizonte **não é agendada agora**.

**O que fazer**

```bash
docker compose exec -T web python manage.py shell -c "
from django.utils import timezone
from planner.services.perfis import perfil_local
from planner.services import replanejamento as R
for n in R.replanejar(perfil_local(), agora=timezone.now()).res.nao_alocado:
    print(f'{n.minutos_restantes:4d}min  {n.tarefa_titulo[:50]}')
    print(f'        {n.motivo}')
"
```

**O que observar** — cinco tarefas de estudo (PP2, Redes Av2, ASL P3 e as duas
recuperações) aparecem como não alocadas, com o motivo explicando. Elas entram
sozinhas no plano quando dezembro se aproximar.

**A decisão é sua:** prefere (a) o comportamento atual — some do plano, com
motivo, e volta mais perto; ou (b) agendar mesmo assim no fim do horizonte, para
ver o bloco reservado na agenda desde já? Eu escolhi (a) porque (b) coloca no
calendário sessões numa data que ninguém pretende cumprir. Se preferir (b), é
uma linha.

**Sinal de problema:** alguma tarefa **não** de estudo aparecendo nessa lista, ou
uma tarefa de estudo com prazo **antes** de 15/11 aparecendo aqui.

---

## 3. O calendário no navegador *(interface — eu não consigo ver)*

**O que fazer**

1. `cd ../../frontend/Planejamento_Frontend && npm run dev`
2. Navegue até **setembro e outubro**, onde estão os estudos já agendados.
3. Clique numa sessão de estudo e abra o painel.
4. Ligue o botão **Editar** na topbar e arraste uma sessão para outro dia.

**O que observar**

- As sessões de estudo aparecem coladas nas provas, não espalhadas.
- Nada quebrou visualmente com os 6 campos novos na API — o front deve
  **ignorá-los** silenciosamente (ele não os mapeia).
- O arrasto continua funcionando (lembre: só no modo **Editar**, e ligar
  *Planejar* desliga *Editar* sem avisar).

**Sinal de problema:** erro no console do navegador mencionando campo
desconhecido, evento aparecendo em duplicado, ou o painel abrindo vazio.

---

## 4. Conversar com o agente *(qualidade subjetiva de texto)*

Eu testei que as ferramentas funcionam e que o texto gerado por código não vaza
jargão. O que **não** dá para automatizar é se a conversa é útil.

**O que fazer** — na interface (painel do agente) ou pelo terminal:

```bash
cd /tmp/claude-1000/-home-taronky-Documents-Projetos-Planner/86c5d134-65e5-4a22-88a3-293e374650fd/scratchpad
python3 chat.py teste-humano-1 "crie uma tarefa de estudo para a prova de X, 4 horas, prazo dia 30/09, e deixe para perto da prova"
```

**O que observar**

- Ele usa `estrategia=TARDE` sozinho? (é a novidade do PR C na ferramenta)
- A resposta está em português comum — **sem UUID, sem nome de campo**?
- Ele inventa alguma coisa que você não pediu?

**Sinal de problema:** qualquer id ou nome de campo na resposta ao usuário. Se
aparecer, me mande a frase literal — é falha da guarda e vira teste.

> ⚠️ **Expectativa calibrada:** o 7b local **não** vai traduzir a descrição em
> texto livre (medido duas vezes — ver `contexto-fase1-prc.md` §4). Se você
> escrever "só de manhã" na descrição de uma tarefa, o plano vai voltar com
> `leitura: [{... "entendi": []}]`. Isso é o comportamento **correto** do
> sistema diante de um modelo fraco, não um bug. Essa camada só rende com modelo
> forte — decisão D6.

---

## 5. Aplicar de verdade *(decisão sobre dado real)*

Até aqui nada gravou. Se você gostou do plano do item 1:

```bash
# simula e mostra o diff (não grava)
curl -s -X POST http://localhost:8000/api/v1/planejamento/replanejar \
  -H 'Content-Type: application/json' -d '{}' | head -c 600

# grava
curl -s -X POST http://localhost:8000/api/v1/planejamento/replanejar/aplicar \
  -H 'Content-Type: application/json' -d '{}' | head -c 600
```

**O que observar:** o segundo comando devolve `eventos_criados` e
`eventos_removidos`. Confira no calendário se bate com o que você aprovou.

**Sinal de problema:** número de eventos removidos maior que o de criados sem
explicação no diff — aí restaure o dump e me avise.

**Para desfazer:**

```bash
docker compose exec -T db psql -U planejador -d planejador -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose exec -T db psql -U planejador -d planejador < ~/backup-antes-teste.sql
```

---

## O que eu já provei — não precisa refazer

| Verificado | Como |
| --- | --- |
| Solver: `TARDE` ancora no fim, recua só quando falta espaço, respeita prazo e grid de 15min | 45 testes em `test_planejamento_parametros.py` |
| Limites duros não relaxam; janela vazia vira `nao_alocado` com motivo próprio | idem |
| `TARDE` ignora `buffer_dias`; `CEDO` continua respeitando | idem |
| `TARDE` além do horizonte não agenda; `CEDO` continua agendando | 3 testes novos |
| Defaults nulos reproduzem o plano antigo sessão a sessão | idem |
| API: 6 campos novos, validação de incoerência (6 casos de 400) | idem |
| `aplicar_plano`: persiste, resumo digerido, 2ª chamada não duplica, nada grava em erro | 11 testes em `test_agente_aplicar_plano.py` |
| MCP: `aplicar_plano` registrada e encadeando calcular→aplicar | 4 testes + container rebuildado (13 tools) |
| Vocabulário: 15 frases, nenhuma com jargão; a guarda rejeita o caso real que vazou | 30 testes em `test_vocabulario.py` |
| Texto livre: guarda-corpo dos knobs, leitura sempre reportada, ≤3 perguntas por impacto | 40 testes em `test_descricao_para_knobs.py` |
| Precedência: campo explícito da tarefa vence a inferência da IA | idem |
| Comportamento do 7b real com descrição em texto livre | 2 rodadas manuais, banco real, rollback |
| Replanejar preserva os parâmetros (o pool não perde `TARDE`) | teste dedicado |

**Suíte: 425 testes**, `ruff` e `black` limpos, sem migration pendente.
