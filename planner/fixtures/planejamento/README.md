# Planejamentos de ensino transcritos

Um JSON por disciplina por semestre, lido por
`manage.py importar_planejamento_ensino`. **É fonte da verdade, não insumo
descartável** (decisão D5 do gate da Fase 1.2): semestre que vem o arquivo é
*editado*, não redigitado, e o diff mostra o que a coordenação mudou no meio do
caminho.

Nome do arquivo: `<sigla>-<semestre>.json` — ex. `asl-2026-2.json`.

## Forma

```json
{
  "disciplina": {
    "codigo": "ELEQ30",
    "nome": "Análise de Sistemas Lineares",
    "sigla": "ASL",
    "classe": "Aula",
    "rastrear_conclusao": false,
    "descricao": "opcional — vale o semestre inteiro (ementa, professor, critério)"
  },
  "semestre": "2026-2",
  "data_inicio": "2026-08-17",
  "data_fim": "2026-12-17",
  "recorrencias": [
    { "dias": [0], "inicio": "15:50", "fim": "17:30" },
    { "dias": [3], "inicio": "15:50", "fim": "18:40" }
  ],
  "prefixo_avulsos": "ASL — ",
  "datas": [
    { "data": "2026-08-20", "conteudo": "Apresentação da disciplina" },
    { "data": "2026-09-17", "conteudo": "Aulas 1 a 5", "prova": "Prova Teórica 1" },
    { "data": "2026-11-26", "conteudo": "Laboratório", "entrega": "Entrega da Prática 3" },
    { "data": "2026-11-02", "sem_aula": "Feriado — Finados" }
  ]
}
```

| Campo | O que faz |
| --- | --- |
| `recorrencias[].dias` | `0=seg … 6=dom`. **Cada dia da semana só pode aparecer em uma recorrência** — senão não há como saber em qual série a data entraria |
| `recorrencias[]` (várias) | uma disciplina com durações diferentes por dia precisa de mais de uma; `RegraRecorrencia` guarda um horário só |
| `data_inicio` / `data_fim` | limites do semestre. A série não gera nada fora disso |
| `prefixo_avulsos` | opcional; default `"<sigla> — "`. Só usado por `--substituir` |
| `conteudo` | vira a **descrição** daquela data. O título do bloco continua sendo o da disciplina |
| `prova` | vira o **título** do dia e troca a classe para `Prova` (é a cor que sinaliza) |
| `entrega` | vira o **título** do dia e **não** mexe na classe |
| `sem_aula` | marca a data como pulada; o texto fica registrado na descrição |

## A regra que mais importa

**O JSON é a autoridade sobre quais datas têm aula.** A série importada usa
`ignorar_feriados=False`, e o comando marca como pulada **toda data que a
recorrência gera e este arquivo não lista**. Consequências:

- aula marcada num feriado **acontece**, porque o arquivo a listou;
- recesso **some**, porque o arquivo não o listou;
- **data esquecida na transcrição vira "sem aula" em silêncio.** O relatório
  conta as auto-puladas justamente por isso — número alto ali é sinal de
  transcrição incompleta, não de semestre curto.

Uma data que não seja dia de aula da disciplina (dia da semana errado, ou fora
do período) é **recusada**: a ocorrência ficaria no banco sem nunca aparecer.

## Uso

```bash
# simula e mostra o relatório; não grava nada
python manage.py importar_planejamento_ensino planner/fixtures/planejamento/asl-2026-2.json

# grava
python manage.py importar_planejamento_ensino <arquivo> --aplicar

# troca os eventos avulsos da disciplina pela série (a migração do PR C)
python manage.py importar_planejamento_ensino <arquivo> --aplicar --substituir
```
