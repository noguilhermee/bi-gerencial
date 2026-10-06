# 🤖 Regras Oficiais do Repositório (`AGENTS.md`)

Este arquivo define o conjunto de diretrizes, regras de negócio e convenções arquiteturais que devem ser **rigorosamente seguidas por qualquer assistente de IA ou desenvolvedor** ao trabalhar ou modificar este repositório.

---

## 📁 1. Estrutura de Pastas e Nomenclatura
- **Regra**: Todas as pastas e subpastas do projeto devem obrigatoriamente usar nomes em **MINÚSCULAS** (lowercase).
- **Estrutura Oficial**:
  - `data/input/` (Bases brutas de entrada)
  - `data/output/` (Arquivos processados, logs, figuras e relatórios compilados)
    - `data/output/processed/`
    - `data/output/figures/`
    - `data/output/html/`
    - `data/output/logs/`
  - `scripts/` (Notebooks Jupyter `.ipynb`, `executar_pipeline.py`)
    - `scripts/assets/` (Logos, favicons, arquivos CSS/JS)
    - `scripts/config/` (Arquivos de configuração e ambiente: `config.yaml`, `.env.example`)
    - `scripts/functions/` (Módulos auxiliares Python: `function.py`, `__init__.py`)
  - `app/` (Aplicação Web, API serverless e interface gerencial)
    - `app/api/`
    - `app/public/`

---

## 🐍 2. Importações Padronizadas nos Notebooks (`scripts/*.ipynb`)
- **Regra**: Todo notebook em `scripts/` deve incluir o bloco de setup padronizado na sua **primeira célula de código**, importando todas as funções utilitárias do módulo `functions.function`.

- **Bloco de Setup Padrão**:
```python
from __future__ import annotations
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from IPython.display import HTML, Markdown, display

caminho_atual = Path.cwd().resolve()
for candidato in [caminho_atual, *caminho_atual.parents]:
    if (candidato / "scripts").is_dir() and ((candidato / "data").is_dir() or (candidato / "app").is_dir()):
        raiz_projeto = candidato
        break
    if (candidato / "SCRIPTS").is_dir() and (candidato / "DATA").is_dir():
        raiz_projeto = candidato
        break
else:
    raise FileNotFoundError("Não foi possível localizar a raiz do projeto.")

for p in [
    raiz_projeto,
    raiz_projeto / "scripts",
    raiz_projeto / "scripts" / "functions",
    raiz_projeto / "SCRIPTS",
    raiz_projeto / "SCRIPTS" / "FUNCTIONS",
]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from functions.function import (
        aplicar_estilo_listrado_xlsx,
        aplicar_formatacao_excel,
        buscar_arquivo_mais_recente,
        carregar_config_referencia,
        carregar_env,
        consultar_tabela_supabase,
        converter_data_excel,
        converter_numero_br,
        detectar_raiz_projeto,
        dividir_seguro,
        exportar_varias_abas_xlsx,
        exportar_xlsx_formatado,
        extrair_data_nome_arquivo,
        garantir_colunas,
        ler_aba_excel_flex,
        criar_cliente_supabase,
        normalizar_texto,
        obter_cliente_supabase,
        renomear_colunas_existentes,
    )
except ImportError:
    from FUNCTIONS.function import (
        aplicar_estilo_listrado_xlsx,
        aplicar_formatacao_excel,
        buscar_arquivo_mais_recente,
        carregar_config_referencia,
        carregar_env,
        consultar_tabela_supabase,
        converter_data_excel,
        converter_numero_br,
        detectar_raiz_projeto,
        dividir_seguro,
        exportar_varias_abas_xlsx,
        exportar_xlsx_formatado,
        extrair_data_nome_arquivo,
        garantir_colunas,
        ler_aba_excel_flex,
        criar_cliente_supabase,
        normalizar_texto,
        obter_cliente_supabase,
        renomear_colunas_existentes,
    )

config_ref = carregar_config_referencia(raiz_projeto)
print(f"Raiz do projeto: {raiz_projeto}")
print(f"Configuracao carregada: {config_ref}")
```

---

## ⏱️ 3. Padrão de Nomenclatura e Timestamp de Exportação
- **Regra**: Todo arquivo gerado/exportado (planilhas Excel, relatórios HTML, figuras, Parquet) deve usar obrigatoriamente a data e hora exata da exportação (`%Y_%m_%d_%H%M%S`).
- **Template**:
```python
DATA_EXPORTACAO = datetime.now().strftime("%Y_%m_%d_%H%M%S")
CAMINHO_ARQUIVO = PASTA_SAIDA / f"{DATA_EXPORTACAO}_nome_do_arquivo.ext"
```

---

## 📚 4. Preservação do Histórico de Saída (`data/output/`)
- **Regra**: Nunca apagar ou sobrescrever arquivos históricos anteriores na pasta `data/output/`.

---

## 🚫 5. Execução sem Arquivos Duplicados
- **Regra**: O script `scripts/executar_pipeline.py` deve salvar as alterações diretamente nos arquivos `.ipynb` originais da pasta `scripts/`. **Nunca criar nem manter arquivos duplicados com o sufixo `_executado.ipynb`**.

---

## 🔄 6. Regra Oficial de Versionamento Git (Sincronização Dupla de Perfil)
- **Regra Absoluta**: O assistente de IA **NUNCA deve executar comandos de `git commit` ou `git push` diretamente**.
- **Sincronização Dupla**: Os projetos devem estar obrigatoriamente configurados para salvar e sincronizar o código simultaneamente nos dois perfis no GitHub:
  - Perfil Pessoal: `noguilhermee`
  - Perfil Organizacional: `LaborRural`
- **Configuração de Push**: O remote `origin` deve conter múltiplos `pushurl` cadastrados para que o comando `git push origin main` envie as alterações para os dois perfis simultaneamente.
- **Obrigação**: Ao concluir etapas significativas, refatorações ou correção de bugs, o assistente deve **obrigatoriamente sugerir e exibir os comandos Git e Vercel exatos** (`git add .`, `git commit -m "..."`, `git push origin main` e `npx vercel --prod`) formatados em bloco de código para que o próprio usuário revise e execute no terminal.
- **Comando em Linha Única**: Sempre que for tecnicamente possível encadear os comandos (`git add .`, `git commit -m "..."`, `git push origin main`), o assistente deve apresentá-los em **uma única linha** (ex.: usando `&&`), para que o usuário execute tudo de uma vez só no terminal.

---

## 🗓️ 7. Centralização de Datas e Mês de Referência (`config.yaml`)
- **Regra**: O mês de referência e parâmetros de filtro temporal **nunca devem ser hardcoded** nos notebooks ou scripts.
- **Fonte Única da Verdade**: Lidos exclusivamente de `scripts/config/config.yaml` através de `carregar_config_referencia(raiz_projeto)`.

---

## 🔒 8. Acesso ao Supabase (Leitura e Inserção para IA / Escrita Controlada via Pipeline ETL)
- **Escopo do Assistente de IA**: O assistente de IA pode **CONSULTAR (`SELECT`) e INSERIR dados (`INSERT`)** no Supabase sem pedir autorização prévia.
- **Criação exige confirmação**: Antes de **criar qualquer objeto** no banco (tabelas, colunas, views, funções, triggers, índices, policies, buckets etc.), o assistente deve **perguntar ao usuário e aguardar aprovação explícita**, descrevendo o que será criado e a nomenclatura proposta (seguindo a regra 10).
- **Demais alterações**: `UPDATE`, `DELETE`, `ALTER`, `DROP` ou `TRUNCATE` em objetos já existentes podem ser executados pelo assistente sem pedir autorização prévia.
- **Escopo do Pipeline ETL**: Os scripts oficiais de pipeline (`reconciliar_movimentacao_e_ativos.py`, `ETL_BI_LR.ipynb`, etc.) possuem autorização para executar operações controladas de gravação/upsert exclusivamente nas tabelas gerenciadas pelo projeto (`sq_*`, `pro_*`, `elabore_*` e `tab_*`).

---

## ⚡ 9. Respostas de Análise de Logs (`análise:`)
- **Regra**: Quando a mensagem do usuário for iniciada por ou contiver `análise:` acompanhada de um log de execução, o assistente deve fornecer **exclusivamente a análise técnica direta do log** (status, volume de registros, métricas de tempo e eventuais erros).
- **Exceção de Mensagens**: Nestas respostas de análise direta, **não recomendar passos de versionamento Git**, mantendo a resposta estritamente focada na avaliação técnica.

---

## 🏛️ 10. Padrão Oficial de Nomenclatura de Tabelas no Supabase (`lr-analytics-db`)
- **Regra**: No banco de dados central `lr-analytics-db` (Workspace `LaborRural Interno`), todas as tabelas devem obrigatoriamente seguir a taxonomia de prefixos por origem e camadas analíticas:
  - **Estrutura**: `<prefixo>_<camada>_<entidade>`
  - **Camada raw (prefixo = origem do dado)**: a tabela guarda o dado como veio da fonte e preserva o histórico.
    - `sq_raw_*`: SmartQuestion (ex: `sq_raw_visitas`, `sq_raw_vinculos`).
    - `pro_raw_*`: Procampo (ex: `pro_raw_fazendas`, `pro_raw_visitas`).
    - `elabore_raw_*`: Elabore (ex: `elabore_raw_consistencia_mensal`, `elabore_raw_consistencia_anual`).
    - `meta_raw_*`: Metas.
  - **Camada de consumo (prefixo `tab_`)**: tabelas que misturam as origens e são lidas pelo BI Gerencial.
    - `tab_fato_*`: Tabelas fato analíticas (ex: `tab_fato_visitas`, `tab_fato_carteira_mensal`, `tab_fato_consistencia`, `tab_fato_movimentacao`, `tab_fato_economico`).
    - `tab_dim_*`: Dimensões com mais de uma origem (ex: `tab_dim_fazendas_ativas`).
    - `tab_base_*`: Snapshots periódicos e bases congeladas mês a mês (ex: `tab_base_metas_mensal`).
  - **Produção e território** (slides 5 e 6 do BI), migration `data/input/2026_09_30_ddl_producao_localizacao.sql`, gravada por `camada_consumo.py`:
    - `tab_fato_producao_mensal`: 1 linha por fazenda × mês (última visita do mês com medição). Traz volume, produtividade e qualidade do leite já tratados: volume suspeito é marcado/imputado (`producao_suspeita`, `producao_imputada`), qualidade fora da faixa plausível vira nula (`qualidade_descartada`) e os limites de CCS/CPP viram flags (`ccs_acima_limite`, `cpp_acima_limite`). Limites e faixas ficam em `config.yaml` (bloco `producao:`), incluindo `agroindustrias_sem_ccs_confiavel` (CCS da Danone em escala divergente).
    - **Coordenada das fazendas**: colunas `latitude`, `longitude`, `origem_coordenada` e `coordenada_corrigida` em `tab_dim_fazendas_ativas` (migration `data/input/2026_09_30_ddl_coordenadas_dim.sql`), sem tabela própria. Alimentada pelas raws: SmartQuestion = "Latitude/Longitude (Produtor)" das planilhas `LISTA_GERAL_VISITAS*.xlsx` (módulo `coordenadas_visitas.py`, gravada por `reconciliar_movimentacao_e_ativos.py` em `sq_raw_fazendas_grupo` e na dim); Procampo = `pro_raw_fazendas` (gravada por `publicar_dim_procampo.py`). `camada_consumo.py` fecha o quadro: fonte primária (visitas/Procampo) > Elabore > posição aproximada pelo município (`origem_coordenada = MUNICIPIO`, mediana das outras fazendas da cidade). Toda coordenada passa por `regras_negocio.sanear_coordenada` (troca de lat/long, sinal perdido, zero e fora da UF). Fazendas sem coordenada válida ou com o Elabore divergindo mais de 5 km da fonte primária saem em `data/output/logs/*_localizacao_fazendas_conferencia.xlsx` para corrigir o cadastro na origem, nunca no frontend.
  - **Dimensões cadastrais** (consultor, região, agroindústria) também ficam na camada de consumo: `tab_dim_consultor`, `tab_dim_regiao`, `tab_dim_agroindustria` (antes `sq_dim_*`, renomeadas em 2026-09-29 pelo script `data/input/2026_09_29_renomear_dims_e_coluna_sem_lancamento_elabore.sql`).
  - **Relacionamento das dimensões (filtros do dashboard)**, migration `data/input/2026_09_29_refatorar_dims_relacionamento.sql`:
    - `tab_dim_agroindustria`: `nome_bi` é o nome exato gravado nas fatos (ex.: `LATICINIOS PORTO ALEGRE (LPA)`); `exibir_no_bi = false` esconde agroindústrias inativas.
    - **Região × Regional**: a coluna `regiao` das fatos (filtro "Região" do BI) é o **nome completo da UF da fazenda** (`regras_negocio.nome_uf`, ex.: `SERGIPE`, `MINAS GERAIS`) para todas as agroindústrias, inclusive a Piracanjuba (UF: carteira do mês > Elabore > vínculo > LISTA_GERAL > base do Procampo), ex.: LR09765 tem regional `ALAGOAS/SERGIPE` e região `SERGIPE`. A **regional** (agrupamento da agroindústria, `tab_dim_regiao`: `ALAGOAS/SERGIPE`, `CCS - CENTRO SUL`...) fica na coluna `regional` de todas as `tab_fato_*` (migration `data/input/2026_10_05_add_coluna_regional.sql`) e tem filtro próprio no dashboard. **Na tela**, `regiao` aparece como "Estado" e `regional` como "Região"; na tela de território o botão "Estado | Região" do mapa escolhe o agrupamento do mapa e dos gráficos (a API devolve `regioes` e `regionais`). Fazenda sem UF conhecida vira `NAO INFORMADA` e sai no relatório de divergências (`UF_NAO_INFORMADA`). Regras em `Contexto.regiao_de` e `Contexto.regional_de` (`camada_consumo.py`).
    - `tab_dim_regiao`: 1 linha por (`id_agroindustria`, regional canônica). Uma regional só é válida para as agroindústrias que a possuem aqui.
    - `tab_dim_regiao_alias`: nome bruto (SmartQuestion/Elabore/Procampo) → região canônica. Novo nome bruto entra aqui, nunca no ETL nem no frontend. Coluna `uf` = sigla(s) da UF do alias, várias separadas por `/` em ordem alfabética (ex.: `AL/SE`), pela migration `data/input/2026_10_06_add_uf_regiao_alias.sql`.
    - `tab_dim_consultor`: `chave_consultor` = nome sem acento e maiúsculo (mesma normalização de `regras_negocio.chave`); o ETL cria os consultores que não existem no Elabore (`origem = 'BI'`).
    - `tab_dim_consultor_atuacao`: ponte consultor × agroindústria × região (UF) × regional × mês, gravada por `camada_consumo.py` (`id_regiao` = id da regional; coluna `regional` pela migration `data/input/2026_10_05_add_regional_consultor_atuacao.sql`); a API a expõe em `filterOptions.relacoes` (`[mês, agroindústria, região, consultor, regional]`) e os filtros de agroindústria, região, regional e consultor se restringem por ela.
    - Uma região que não pertence à agroindústria da fazenda vira `NAO INFORMADA` e é listada em `data/output/logs/*_divergencias_dimensoes.xlsx` para correção na origem (cadastro), nunca no frontend (regra 12).
  - **Colunas**: Sempre em `snake_case` minúsculo com timestamps `data_processamento` e chaves `id_composto`.

---

## 🛑 11. Execução de Scripts do Projeto (Solicitação Obrigatória ao Usuário)
- **Regra Absoluta**: O assistente de IA **NUNCA deve executar scripts do projeto de forma autônoma no terminal** (sejam rotinas de pipeline, scripts de carga, reconciliação, migração ou execução de notebooks).
- **Procedimento Obrigatório**:
  1. Preparar e editar os códigos necessários no repositório.
  2. Apresentar o comando exato de execução em bloco de código formatado para o usuário (ex: `python scripts/...` ou `python scripts/executar_pipeline.py`).
  3. Descrever claramente o objetivo do script e os impactos esperados.
  4. **Pedir formalmente que o próprio usuário execute o comando em seu terminal**, aguardando o retorno ou logs fornecidos por ele.

---

## 🛠️ 12. Proibição de Correção Paliativa no Frontend/API e Foco no ETL
- **Regra Absoluta**: **NUNCA fazer "gambiarras", tratamentos paliativos ou manipulações artificiais de dados no front-end ou nos arquivos JS de API** (`app/api/*`, `app/public/*`).
- **Procedimento Obrigatório**: Caso seja identificada qualquer inconsistência, divergência de contagem ou ausência de dados na interface, a correção deve ser realizada na raiz da fonte de dados (nos scripts de ETL/Python). O assistente de IA deve **obrigatoriamente estruturar um plano de implementação para ajustar a regra diretamente nos scripts de ETL** (`scripts/`), garantindo que o banco de dados (Supabase) permaneça como a única fonte da verdade e que os dados sejam reconciliados e persistidos corretamente na origem.

---

## 👤 13. Exceção de Consultor: Marcos Paulo de Pinho Flecha (sem lançamento no Elabore)
- **Regra de negócio (BI Gerencial)**: o consultor **Marcos Paulo de Pinho Flecha** é uma exceção em relação aos demais, pois **não faz lançamento de dados no Elabore**.
- **Efeito**: em indicadores de consistência, como **"Número de fazendas consistentes no mês"**, as fazendas deste consultor **não devem entrar no cálculo**. Vale para numerador e denominador, para não distorcer os percentuais.
- **Continua valendo**: ele permanece na carteira, nas visitas e na movimentação. A exceção se restringe aos indicadores que dependem de dados do Elabore.
- **Co-consultoria**: a fazenda só fica de fora quando **todos** os consultores de campo do grupo são isentos. Se houver um co-consultor que lança no Elabore, ela continua contando.
- **Como é aplicada**: o ETL (`camada_consumo.py`) grava `sem_lancamento_elabore = true` em `tab_fato_carteira_mensal` e `tab_fato_consistencia`; a API apenas ignora essas linhas nos cálculos de consistência (`overview.js`, `consistency.js`). A coluna `excecao` (lista de produtores exceção) é independente e não foi alterada.
- **Fonte única da verdade**: `CONSULTORES_SEM_LANCAMENTO_ELABORE`, `eh_consultor_sem_lancamento_elabore()` e `eh_grupo_sem_lancamento_elabore()` em `scripts/functions/regras_negocio.py`. Novos consultores isentos são adicionados nesse conjunto, nunca no frontend nem na API (regra 12).

---

## 🔠 14. Texto no Supabase: MAIÚSCULO e SEM ACENTO
- **Regra**: todo valor texto gravado no Supabase é **maiúsculo e sem acento** (ex.: `LATICINIOS PORTO ALEGRE (LPA)`, `NAO INFORMADA`, `SAIDA`, `CONSISTENTE`). Pontuação e espaços são mantidos.
- **Como é aplicada**: o cliente devolvido por `obter_cliente_supabase()` / `criar_cliente_supabase()` (`scripts/functions/function.py`) aplica `normalizar_registros()` em todo `insert`, `upsert` e `update`. **Nunca usar `supabase.create_client` direto** — scripts e notebooks criam o cliente só por essas funções.
- **Exceção**: colunas de identificador (`id`, `id_*`, `*_id`, `idfazenda*`) ficam como vieram (hash, UUID do Elabore), conforme `eh_coluna_identificador()`. Valores não texto não são alterados.
- **Comparações**: o Python compara texto lido do banco com `regras_negocio.chave()`; literais de valores na API/frontend (`app/api/*`, `app/public/js/*`) seguem o mesmo padrão (`'NAO INFORMADA'`, `'SAIDA'`, `'CONSISTENTE'`, `'SEM DADOS'`).
- **Dados antigos**: convertidos por `data/input/2026_09_30_normalizar_texto_maiusculo_sem_acento.sql`.



