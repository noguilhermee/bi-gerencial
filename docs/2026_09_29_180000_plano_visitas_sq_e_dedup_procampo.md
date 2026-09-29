# Plano de implementação — itens 1 e 2 da comparação dashboard × log (29/09/2026)

## Item 1 — Visitas do SmartQuestion ausentes depois de 23/09

### Diagnóstico
- No dashboard (Set/26), visitas do SmartQuestion por dia: 23/09 = 33 e **0** em 24, 25, 26 e 28/09. As do Procampo continuam entrando.
- Efeito: a cobertura de Set (61,9%) e as visitas do mês (884) estão subestimadas (Ago: 78,9% e 1.120).
- Causa: `sq_raw_visitas` não é recarregada desde a carga completa de 24/09.
  - `db/output/logs/2026_09_28_102725_execucao_agendada.log`: o pipeline agendado de 28/09 falhou na célula
    `etl_vinculos(DIR_BD_SMARTQUESTION, apenas_testar=False)` do `ETL_BI_LR.ipynb` (timeout de 1800 s, 3 h 12 min de execução).
  - Em `scripts/executar_pipeline.py`, a carga de visitas (`carregar_historico_visitas`) só roda **depois** do notebook.
    Notebook falhou → visitas, reconciliação e camada de consumo não rodam.
  - Os exports já estão atualizados na pasta (LISTA_GERAL_VISITAS.xlsx 29/09 01:02; LISTA_REGENERA/ALVOAR/CCPR/LPA 29/09 02:00–03:03).
    O que falta é carregá-los.

### Ação imediata (sem alterar código)
1. `python scripts/functions/carregar_historico_visitas.py` — upsert idempotente em `sq_raw_visitas`
   (id_composto = sha256("ATEND_<id>")); confere ao final se todos os atendimentos dos arquivos estão no Supabase.
   Deve imprimir "Total consolidado ... a 2026-09-2x" (maior `data_visita` dos arquivos).
2. `python scripts/functions/camada_consumo.py` — republica `tab_fato_*`.
3. `python scripts/functions/metadata_tracker.py` — atualiza o painel de proveniência.
4. Validar: visitas SQ/dia em 24–28/09 > 0 e cobertura de Set/26 sobe. Uma visita datada de 06/10/2026
   continua excluída (`EXCLUIDA_DATA_VISITA_FUTURA`) — corrigir a data no SmartQuestion.

### Correção da causa (a decidir)
- Descobrir por que `etl_vinculos` passou de 30 min (em 24/09 rodou normal). Hipóteses: travamento de leitura de rede/Excel,
  lock em `BD_BI_VINCULOS_COMPLETO.xlsx` (786 KB, regravado 29/09 02:05) ou volume de upserts.
  Pedir o log da célula (saída do notebook) antes de mexer.
- Em `executar_pipeline.py`, desacoplar o passo 3 do sucesso do notebook: a carga de visitas (raw) não depende de `etl_vinculos`.
  Hoje uma falha em vínculos deixa o dashboard sem visitas novas por dias. Proposta: rodar visitas e Procampo mesmo com o notebook
  falhando, e bloquear apenas a camada de consumo se a raw de vínculos estiver desatualizada.
- Alerta de defasagem: no fim da `camada_consumo`, imprimir (e falhar em modo estrito) quando `max(data_visita SQ)` estiver > 2 dias
  atrás do dia da execução.

## Item 2 — Entradas duplicadas do Procampo e saldo ≠ variação da carteira

### Diagnóstico
- `tab_fato_movimentacao`: Set/26 tem 19 pares idênticos de ENTRADA (todos PROCAMPO) → 113 entradas = 94 únicas.
  Ago/26: 23 pares → 113 = 90 únicas. O card de Entradas, o Saldo e o gráfico de entradas/saídas herdam a duplicidade.
- Hipótese (a confirmar): `carregar_procampo.montar_raw_movimentacao` (linha 346) inclui `aba_origem` no `id_composto`.
  Um mesmo "Inclusão do produtor" presente em ATIVOS e em ROTATIVIDADE (ou repetido com `data_conclusao` diferente) vira
  duas linhas em `pro_raw_movimentacao`, e `camada_consumo.construir_movimentacao_procampo` (linhas 803–853) as publica sem deduplicar.
- Verificação (somente SELECT, no Supabase):
  ```sql
  select codigo_lr, status_movimentacao, data_inicio,
         array_agg(aba_origem order by aba_origem) as abas, count(*) as n
  from pro_raw_movimentacao
  group by codigo_lr, status_movimentacao, data_inicio
  having count(*) > 1
  order by n desc;
  ```

### Implementação proposta (ETL, `scripts/functions/camada_consumo.py`)
1. Em `construir_movimentacao_procampo`, antes de retornar, deduplicar por `(codigo_lr, tipo, data_movimentacao)`.
   Manter a linha de ATIVOS para ENTRADA e a de ROTATIVIDADE para SAÍDA; se `aba_origem` não vier em
   `COLUNAS`/`_buscar_tudo` (linhas 189–191), incluí-la na leitura.
2. Gerar `id_composto` estável a partir da chave deduplicada: `sha256("PROCAMPO_MOV_<codigo_lr>|<tipo>|<data>")`,
   para que a republicação substitua as linhas antigas (o `sincronizar_tabela` remove as obsoletas).
3. Imprimir "N eventos duplicados do Procampo unificados", no mesmo padrão do aviso de eventos sem data.
4. Não mexer em `pro_raw_movimentacao` (raw preserva a fonte).
5. Cosmético: Procampo grava `data_solicitacao = None`, e o dashboard mostra "-". Avaliar preencher com a data do evento.

### Saldo × variação da carteira (segunda etapa)
Mesmo sem duplicatas, entradas − saídas ≠ variação de `tab_fato_carteira_mensal`
(Set: +72 esperado sem duplicatas vs +54; Ago: +83 vs +51; Mai: +26 vs −11; Mar: +5 vs −14).
Depois da deduplicação, reconciliar por código em uma consulta única: farms que aparecem na carteira do mês M e não em M−1
(entradas reais) × `tab_fato_movimentacao` ENTRADA do mês M, e o inverso para saídas. As diferenças costumam vir de:
"Inclusão de propriedade" (mesmo produtor, nova fazenda), reativações, e saídas sem `data_movimentacao` no mês certo.
Resultado dessa análise define se o card "Saldo" deve usar movimentação ou variação da carteira.

### Validação
- `tab_fato_movimentacao`: 0 grupos `(codigo_lr, tipo, data_movimentacao)` com mais de uma linha.
- Dashboard Set/26: entradas 113 → 94; saldo +72.
