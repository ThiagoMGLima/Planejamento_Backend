# Captura no celular via Notion → Inbox

Você anota a tarefa numa **database do Notion** (sempre disponível na nuvem) ao
longo do dia; ao chegar no PC, o backend **puxa** as novas para a Inbox e você
roda o planejador. Fluxo one-way (Notion → backend); o backend nunca fica
exposto, apenas chama a API do Notion.

```
📱 Atalho iOS / app Notion → DB tipada (nuvem) → [PC] → POST /notion/sync → Inbox
   → (completa esforço/classe se faltar) → planejar-ia
```

## 1. Database do Notion

Crie uma database com EXATAMENTE estas propriedades (nomes importam — o backend
casa por nome, ver `planner/services/notion_sync.py`):

| Propriedade | Tipo | Observação |
| --- | --- | --- |
| **Tarefa** | Title | título da tarefa |
| **Prazo** | Date | data (ou data+hora) do deadline |
| **Esforço (min)** | Number | opcional; em branco, você completa no PC |
| **Classe** | Select | opções = as 5 classes: `Aula`, `Estudar`, `Prova`, `Trabalho`, `Tarefas básicas` |
| **Status** | Select | opções `Nova` e `Importada`; **default `Nova`** |

Regras de mapeamento:
- `Classe` casa pelo nome do Select; em branco/desconhecida → tarefa fica sem
  classe (inválida para o planejador até você completar).
- `Prazo` só com data (sem hora) → deadline às `NOTION_DEADLINE_HORA_PADRAO`
  (default 23:59, fuso local).
- O sync só lê páginas com `Status = Nova` e as marca `Importada` (idempotente:
  a mesma página nunca vira duas tarefas, via `Tarefa.origem_externa_id`).

## 2. Integração e credenciais

1. Notion → **Settings → Connections → Develop or manage integrations** → nova
   integração interna. Copie o **Internal Integration Secret**.
2. Na database, **••• → Connections → conecte a integração** (senão a API não a
   enxerga).
3. Pegue o **Database ID** (na URL da database: o hash antes do `?v=`).
4. No `.env` do backend:
   ```
   NOTION_TOKEN=secret_xxx
   NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

## 3. Captura no iPhone

**App do Notion** — quick-add do título e preenche as propriedades. Sem nada a
construir.

**Atalho do iOS (captura guiada, recomendado)** — um Shortcut no Home Screen /
botão de ação / Siri que pergunta os campos e cria a página direto na API do
Notion (funciona de qualquer rede, sem depender do PC):

1. *Pedir Entrada* (Texto) → "Qual a tarefa?"
2. *Pedir Entrada* (Data) → "Prazo?"
3. *Escolher do Menu* → Classe: Aula / Estudar / Prova / Trabalho / Tarefas básicas
4. *Pedir Entrada* (Número) → "Esforço em minutos? (opcional)"
5. *Obter conteúdo da URL* → `POST https://api.notion.com/v1/pages`
   - Headers: `Authorization: Bearer <token>`, `Notion-Version: 2022-06-28`,
     `Content-Type: application/json`
   - Body (JSON):
     ```json
     {
       "parent": { "database_id": "<DATABASE_ID>" },
       "properties": {
         "Tarefa":  { "title": [{ "text": { "content": "<título>" } }] },
         "Prazo":   { "date": { "start": "<data ISO>" } },
         "Classe":  { "select": { "name": "<classe>" } },
         "Esforço (min)": { "number": <minutos> },
         "Status":  { "select": { "name": "Nova" } }
       }
     }
     ```

## 4. Sincronizar no PC

- **App web:** botão "Sincronizar com Notion" (frontend — task em
  `Planejamento_Frontend/tasks/`).
- **Endpoint:** `POST /api/v1/notion/sync` → `{importadas, ignoradas, erros}`;
  `400` se desligado (sem token), `503` se o Notion estiver inacessível.
- **CLI:** `docker compose exec web python manage.py sync_notion`.
