# CLAUDE.md

As regras oficiais deste repositório ficam em `AGENTS.md`, que é a fonte única da verdade para qualquer assistente de IA. O conteúdo é importado abaixo e deve ser seguido integralmente.

@AGENTS.md

## Observações específicas para o Claude Code

- **Git**: não executar `git commit` nem `git push`. Ao concluir etapas significativas, mostrar os comandos (`git add .`, `git commit -m "..."`, `git push origin main`, `npx vercel --prod`) em bloco de código para o usuário executar — sempre que possível, encadeados em **uma única linha** (regra 6).
- **Scripts do projeto**: não rodar pipelines, notebooks, cargas ou migrações pelo terminal. Apresentar o comando exato, explicar objetivo e impacto, e pedir que o usuário execute e devolva os logs (regra 11).
- **Supabase**: leitura (`SELECT`), inserção (`INSERT`) e alteração de objetos existentes (`UPDATE`/`DELETE`/`ALTER`/`DROP`/`TRUNCATE`) liberadas. Criar um objeto novo (tabela, coluna, view, função, policy etc.) ainda exige perguntar antes (regra 8).
- **Correções de dados**: sempre na origem (ETL em `scripts/`), nunca no `dashboard/api/*` ou `dashboard/public/*` (regra 12).
- **`análise:`**: responder apenas com a análise técnica do log, sem sugestão de Git (regra 9).
- Ao editar `AGENTS.md`, as mudanças passam a valer aqui automaticamente; não duplicar regras neste arquivo.

---

## Resumo do Stack

| Camada      | Tecnologia                                        |
|-------------|---------------------------------------------------|
| Frontend    | HTML/CSS/JS puro (SPA, arquivo único `index.html`) |
| Gráficos    | Chart.js v4.4.1 (CDN)                             |
| Fontes      | Google Fonts (Open Sans), Material Symbols Rounded |
| Backend/API | Node.js serverless (Vercel Functions)              |
| Banco       | Supabase (PostgreSQL) — chave `service_role` server-side |
| Hospedagem  | Vercel (produção) + `node server.js` (local)       |
| ETL         | Python (scripts/ — notebooks Jupyter)              |

## Como rodar localmente

```bash
cd dashboard
npm install                      # instala dependências (supabase-js, pg)
cp .env.example .env.local       # preencher com chaves reais
node server.js                   # http://localhost:5050
```

O `server.js` serve arquivos estáticos de `public/` e roteia `/api/*` para os handlers em `api/`.

## Estrutura de pastas

```
bi-gerencial/
├── dashboard/
│   ├── api/                     # Endpoints serverless (Vercel Functions)
│   │   ├── shared.js            # Client Supabase, cache, filtros, utilidades
│   │   ├── overview.js          # KPIs gerais, séries históricas, tabelas
│   │   ├── visits.js            # Visitas por consultor, ranking
│   │   ├── turnover.js          # Movimentação (entradas/saídas/churn)
│   │   ├── consistency.js       # Consistência Elabore (mensal/anual)
│   │   ├── economics.js         # Indicadores econômicos e produção
│   │   ├── production.js        # Slide 5: produção e qualidade do leite (tab_fato_producao_mensal)
│   │   ├── territory.js         # Slide 6: carteira e território (carteira + localização + produção)
│   │   ├── health.js            # Health check (Supabase + Azure PG)
│   │   └── azurePostgres.js     # [LEGADO] conexão direta Azure PostgreSQL
│   ├── public/
│   │   ├── index.html           # SPA — dashboard completo
│   │   ├── css/dashboard.css    # Estilos (design system, light/dark)
│   │   ├── js/main.js           # Lógica principal (~3100 linhas)
│   │   ├── js/charts.js         # Fábrica de gráficos Chart.js
│   │   ├── js/producao.js       # Slides 5 e 6: gráficos, trajetória e mapa (Leaflet); só desenha o que a API devolve
│   │   ├── js/carousel.js       # Modo kiosk (slides para TV)
│   │   └── img/                 # Logos e ícones
│   ├── server.js                # Servidor local (Node.js, porta 5050)
│   ├── vercel.json              # Configuração Vercel (routes, headers, functions)
│   ├── package.json             # Dependências: @supabase/supabase-js, pg
│   └── .env.local               # Variáveis de ambiente (gitignored)
├── scripts/                     # ETL Python (notebooks Jupyter)
│   ├── functions/               # Módulos auxiliares (function.py, regras_negocio.py)
│   └── config/                  # config.yaml, .env.example
├── db/                          # Dados de entrada e saída (gitignored)
├── .claude/agents/              # Subagentes Claude Code
├── AGENTS.md                    # Regras oficiais do repositório
└── CLAUDE.md                    # Este arquivo
```

## Subagentes (`.claude/agents/`)

| Agente              | Arquivo                                | Modelo  | Quando usar                                                       |
|---------------------|----------------------------------------|---------|-------------------------------------------------------------------|
| **supabase-db**     | `.claude/agents/supabase-db.md`        | sonnet  | Alterar schema, queries, views, RPCs, RLS ou gerar migrations     |
| **kpi-validator**   | `.claude/agents/kpi-validator.md`      | sonnet  | Após qualquer mudança em cálculo/query — valida KPIs via SQL      |
| **security-reviewer** | `.claude/agents/security-reviewer.md`| haiku   | Antes de deploys ou após mudanças em endpoints/configs            |
| **ui-dashboard**    | `.claude/agents/ui-dashboard.md`       | sonnet  | Ajustes de layout, gráficos, responsividade, temas, componentes   |

### Fluxo recomendado

1. Alteração de dados/schema → `supabase-db` → `kpi-validator` (validar impacto).
2. Alteração visual → `ui-dashboard`.
3. Pré-deploy → `security-reviewer`.
4. O `kpi-validator` NUNCA edita arquivos — só reporta divergências.
5. O `security-reviewer` NUNCA edita arquivos — só reporta achados.
