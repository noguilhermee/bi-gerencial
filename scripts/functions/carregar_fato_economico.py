# -*- coding: utf-8 -*-
"""
Módulo: carregar_fato_economico.py
Responsável por consolidar, calcular indicadores econômicos e zootécnicos a partir
do relatório de indicadores MENSAIS do Elabore, e executar a carga upsert na
tabela fato 'tab_fato_economico' do Supabase.

Regra de negócio (validada por reconciliação 1:1 contra os indicadores ANUAIS do
Elabore — soma dos 12 meses de cada janela == valor anual, em 100% das janelas
testadas em 2026-09-23):

    COE (Custo Operacional Efetivo) do mês =
        Custo de Concentrado e Mineral (R$)
      + Gasto Total Volumoso (R$) + Custo Forrageira Própria Volumoso (R$)
      + Despesas Mão de Obra CONTRATADA (R$)                        <- MO familiar NÃO entra no COE
      + Medicamentos e Vacinas (R$) + Hormônios (R$) + Reprodução (R$)
      + Acessórios e Despesas Gerais (R$) + Despesas Administrativas (R$)
      + Arrendamento (R$) + Assistência Técnica (R$) + Reparos e Consertos (R$)
      + Impostos e Taxas (R$) + Energia Elétrica (R$) + Combustível (R$)
      + Leite para Bezerras (R$) + Sucedâneo (R$) + Reposição da cama (R$)

    ATENÇÃO: "Material de Ordenha (R$)" existe na planilha mas o Elabore NÃO o
    soma no COE anual (testado: incluí-lo derruba a aderência de 100% para 1,5%).
    Não incluir esta coluna no COE — é uma possível inconsistência do próprio
    relatório Elabore, a reportar à equipe do lr-indicadores-elabore.

    JANELA ANUAL (2026-09-30): a fato só tem fazenda x mês que existe em elabore_raw_consistencia_anual
    (janela de 12 meses terminando naquele mês; fazendas com menos de 12 meses ficam de fora).
    Dali vêm a margem bruta (gross_margin_liter_annual) e o status CONSISTENTE/INCONSISTENTE/OUTLIER
    (gravado em status_consistencia_mensal). margem_bruta_total = margem/L anual x litros do mês.
    Motivo: Receita Bruta da Atividade mensal inclui venda de animais e inflava o R$/L (ex.: LR11943).
    COE, receita, preço e volume seguem mensais, calculados como abaixo.

    Margem Bruta mensal (NÃO usada mais na fato) = Receita Bruta da Atividade (R$) - COE
    volume_leite_mes = Produção Total de Leite (litros)    (NÃO é Volume de Leite Vendido)
    receita_leite_total = Receita Total do Leite (R$)      (Venda + Derivados)

Estrutura da Tabela no Supabase (tab_fato_economico):
- Chave Primária: id_composto (codigo_lr + '_' + mes_referencia)
- Campos: id_composto, codigo_lr, idfazenda, id_propriedade_elabore, nome_produtor,
  nome_consultor, projeto, agroindustria, regiao, mes_referencia, volume_leite_mes,
  volume_leite_vendido, volume_diario_litros, vacas_lactacao, vacas_totais,
  produtividade_l_vl_dia, receita_leite_total, receita_bruta_atividade,
  preco_medio_litro, coe_total_reais, coe_por_litro, margem_bruta_total,
  margem_bruta_por_litro, flag_mb_positiva, coe_concentrado, coe_volumoso,
  coe_mao_de_obra, coe_sanidade, coe_outros, custo_mo_familiar,
  possui_dados_economicos, status_consistencia_mensal, data_associacao,
  data_processamento
"""
from __future__ import annotations

import zipfile

import os
import re
import sys
import math
import shutil
import tempfile
import calendar
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yaml
from dotenv import load_dotenv
from supabase import Client

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def detectar_raiz(caminho_base: Path | None = None) -> Path:
    caminho_atual = (caminho_base or Path(__file__).resolve()).parent
    for candidato in [caminho_atual, *caminho_atual.parents]:
        if (candidato / "scripts").is_dir() and ((candidato / "data").is_dir() or (candidato / "app").is_dir()):
            return candidato
        if (candidato / "SCRIPTS").is_dir() and (candidato / "DATA").is_dir():
            return candidato
    return caminho_atual


raiz_projeto = detectar_raiz()
for p in [
    raiz_projeto,
    raiz_projeto / "scripts",
    raiz_projeto / "scripts" / "functions",
    raiz_projeto / "SCRIPTS",
    raiz_projeto / "SCRIPTS" / "FUNCTIONS",
]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from functions.function import criar_cliente_supabase  # noqa: E402


def carregar_configuracao(raiz: Path) -> dict:
    config_file = raiz / "scripts" / "config" / "config.yaml"
    if not config_file.is_file():
        config_file = raiz / "SCRIPTS" / "CONFIG" / "config.yaml"
    if not config_file.is_file():
        raise FileNotFoundError(f"❌ Arquivo de configuração não encontrado: {config_file}")
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def obter_cliente_supabase(raiz: Path):
    for env_path in [
        raiz / "scripts" / "config" / ".env",
        raiz / "app" / ".env.local",
        raiz / "SCRIPTS" / "CONFIG" / ".env",
        raiz / "APP" / ".env.local",
        raiz / ".env",
    ]:
        if env_path.is_file():
            load_dotenv(env_path)

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise ValueError("❌ Credenciais do Supabase não encontradas no arquivo .env!")

    return criar_cliente_supabase(supabase_url, supabase_key)


def ler_excel_seguro(caminho_arquivo: Path, sheet_name: str | int = 0) -> pd.DataFrame:
    """Lê um arquivo Excel criando uma cópia temporária para evitar locks no Windows."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        shutil.copy2(caminho_arquivo, tmp.name)
        df = pd.read_excel(tmp.name, sheet_name=sheet_name)
        return df
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def localizar_arquivo_recente(diretorio_principal: Path, fallback_dirs: list[Path], padrao_glob: str) -> Path:
    """Busca o arquivo mais recente que casa com o padrão glob."""
    pastas_busca = [diretorio_principal] + fallback_dirs
    for pasta in pastas_busca:
        if pasta.is_dir():
            # Ignora xlsx ainda em gravação por outro processo (zip incompleto).
            arquivos = [a for a in pasta.glob(padrao_glob) if zipfile.is_zipfile(a)]
            if arquivos:
                recente = max(arquivos, key=lambda f: f.stat().st_mtime)
                print(f"📖 Arquivo de indicadores localizado ({recente.parent.name}): {recente.name}")
                return recente

    raise FileNotFoundError(f"❌ Nenhum arquivo '{padrao_glob}' encontrado nas pastas: {[str(p) for p in pastas_busca]}")


def buscar_todos_registros(supabase: Client, tabela: str, select_cols: str = "*") -> list:
    todos = []
    chunk_size = 1000
    offset = 0

    while True:
        try:
            res = supabase.table(tabela).select(select_cols).range(offset, offset + chunk_size - 1).execute()
            if not res.data:
                break
            todos.extend(res.data)
            if len(res.data) < chunk_size:
                break
            offset += chunk_size
        except Exception as e:
            print(f"⚠️ Aviso ao ler tabela '{tabela}': {e}")
            break

    return todos


def converter_numero_br_float(val: Any) -> float:
    """Converte valores em formato BR ("1.500,00", "R$ 3,25") ou float comum para float puro."""
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float, np.integer, np.floating)):
        v = float(val)
        return 0.0 if math.isnan(v) or math.isinf(v) else v
    s = str(val).strip().replace("R$", "").replace("r$", "").replace(" ", "")
    if not s or s.lower() in ["nan", "none", "null", "nat", "<na>"]:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return 0.0 if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return 0.0


def normalizar_texto_maiusculo(valor: Any) -> str | None:
    """Padroniza texto (nome_consultor, nome_produtor): MAIÚSCULAS, sem acentos, espaços únicos.
    Mantém vírgulas/pontuação (ex.: consultor pode trazer múltiplos nomes separados
    por ", " ou ";" — não é papel desta função separar essa lista, só padronizar grafia)."""
    if valor is None or pd.isna(valor):
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("utf-8")
    texto = texto.upper()
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


def sanitize_json_records(records: list[dict]) -> list[dict]:
    """Garante compatibilidade total com JSON e PostgreSQL (sanitiza NaN/Inf para 0.0/None)."""
    int_cols = {"flag_mb_positiva", "possui_dados_economicos"}
    numeric_2dec_cols = {"vacas_lactacao", "vacas_totais"}
    str_limits = {
        "codigo_lr": 50,
        "projeto": 100,
        "agroindustria": 100,
        "regiao": 100,
        "id_composto": 255,
        "nome_produtor": 255,
        "nome_consultor": 255,
        "idfazenda": 36,
        "id_propriedade_elabore": 36,
        "status_consistencia_mensal": 50,
    }

    clean = []
    for record in records:
        clean_row = {}
        for k, v in record.items():
            if v is None or pd.isna(v):
                clean_row[k] = None if k in int_cols or k in str_limits or k == "data_associacao" else 0.0
                continue

            if k in int_cols:
                try:
                    v_flt = float(v)
                    if math.isnan(v_flt) or math.isinf(v_flt):
                        clean_row[k] = None
                    else:
                        clean_row[k] = int(v_flt)
                except Exception:
                    clean_row[k] = None
            elif k in str_limits:
                limit = str_limits[k]
                s_val = str(v).strip()
                clean_row[k] = s_val[:limit] if s_val else None
            elif isinstance(v, (float, np.floating, int, np.integer)):
                v_flt = float(v)
                if math.isnan(v_flt) or math.isinf(v_flt):
                    clean_row[k] = 0.0
                else:
                    clean_row[k] = round(v_flt, 4) if k not in numeric_2dec_cols else round(v_flt, 2)
            else:
                clean_row[k] = v
        clean.append(clean_row)
    return clean


def resolver_coluna_exata(columns: list[str], candidatos: list[str]) -> str | None:
    """
    Resolve o nome exato de uma coluna (case-insensitive, ignorando espaços nas pontas).
    Diferente da busca por substring, evita falsos positivos (ex: 'Concentrado Vendido (R$)'
    casando com o padrão 'concentrado' de custo).
    """
    cols_map = {c.strip().lower(): c for c in columns}
    for cand in candidatos:
        hit = cols_map.get(cand.strip().lower())
        if hit:
            return hit
    return None


def encontrar_nome_coluna(columns: list[str], padroes: list[str]) -> str | None:
    """Busca fuzzy (substring) — usada apenas para campos descritivos/cadastrais, nunca para valores financeiros."""
    cols_map = {c.strip().lower(): c for c in columns}
    for padrao in padroes:
        p_low = padrao.strip().lower()
        if p_low in cols_map:
            return cols_map[p_low]
        for c_low, c_orig in cols_map.items():
            if p_low in c_low:
                return c_orig
    return None


# ---------------------------------------------------------------------------
# Mapa oficial e explícito das colunas financeiras/zootécnicas usadas no COE.
# Usa correspondência EXATA (não substring) para não confundir, por exemplo,
# "Concentrado Vendido (R$)" (receita) com "Custo de Concentrado e Mineral (R$)".
# Cada chave lógica aceita variações de nome (planilha pode ser re-exportada
# com pequenas mudanças de rótulo), mas sempre por nome completo e exato.
# ---------------------------------------------------------------------------
MAPA_COLUNAS_COE = {
    "concentrado_mineral": ["Custo de Concentrado e Mineral (R$)"],
    "volumoso": ["Gasto Total Volumoso (R$)"],
    "forrageira_propria_volumoso": ["Custo Forrageira Própria Volumoso (R$)"],
    "mo_contratada": ["Despesas Mão de Obra Contratada (R$)"],
    "mo_familiar": ["Despesas Mão de Obra Familiar (R$)"],
    "medicamentos_vacinas": ["Medicamentos e Vacinas (R$)"],
    "hormonios": ["Hormônios (R$)"],
    "reproducao": ["Reprodução (R$)"],
    "acessorios_gerais": ["Acessórios e Despesas Gerais (R$)"],
    "despesas_administrativas": ["Despesas Administrativas (R$)"],
    "arrendamento": ["Arrendamento (R$)"],
    "assistencia_tecnica": ["Assistência Técnica (R$)"],
    "reparos_consertos": ["Reparos e Consertos (R$)"],
    "impostos_taxas": ["Impostos e Taxas (R$)"],
    "energia_eletrica": ["Energia Elétrica (R$)"],
    "combustivel": ["Combustível (R$)"],
    "leite_bezerras": ["Leite para Bezerras (R$)"],
    "sucedaneo": ["Sucedâneo (R$)"],
    "reposicao_cama": ["Reposição da cama (R$)"],
}

MAPA_COLUNAS_BASE = {
    "receita_venda_leite": ["Receita com Venda de Leite (R$)"],
    "receita_total_leite": ["Receita Total do Leite (R$)"],
    "receita_bruta_atividade": ["Receita Bruta da Atividade (R$)"],
    "volume_vendido": ["Volume de Leite Vendido (litros)"],
    "producao_total": ["Produção Total de Leite (litros)"],
    "producao_diaria": ["Produção Diária de Leite (litros/dia)"],
    "vacas_lactacao": ["Vacas em Lactação (cabeças)"],
    "vacas_totais": ["Total de Vacas (cabeças)"],
    "produtividade": ["Produção por Vaca em Lactação (litros/vaca/dia)"],
    "dias_no_mes": ["Dias no Mês"],
    "status_consistencia": ["Status de Consistência"],
    "possui_dados_receita": ["Possui Dados de Receita"],
    "possui_dados_rebanho": ["Possui Dados de Rebanho"],
    "possui_dados_despesas": ["Possui Dados de Despesas"],
}


def processar_e_carregar_fato_economico(raiz: Path | None = None) -> int:
    if raiz is None:
        raiz = raiz_projeto

    config = carregar_configuracao(raiz)
    supabase = obter_cliente_supabase(raiz)

    print("\n" + "=" * 70)
    print("🚀 PROCESSANDO TABELA FATO: tab_fato_economico (regra COE validada 1:1 c/ Elabore Anual)")
    print("=" * 70)

    # 1. Carregar cadastro de produtores / vínculos (fonte de verdade de projeto/consultor/data de associação)
    raw_vinculos = buscar_todos_registros(supabase, "sq_raw_vinculos", "*")
    mapa_vinculos = {}
    if raw_vinculos:
        for v in raw_vinculos:
            cod = str(v.get("codigo_lr", "")).strip().upper()
            if cod and cod not in mapa_vinculos:
                mapa_vinculos[cod] = v

    # 1b. Janelas anuais do Elabore (elabore_raw_consistencia_anual, gravada por
    # atualizar_detalhamento_consistencia.py). Só entra na fato a fazenda x mês que tem janela anual
    # (mes_referencia = mês final da janela de 12 meses); margem e status vêm dela.
    raw_anual = buscar_todos_registros(
        supabase, "elabore_raw_consistencia_anual",
        "codigo_lr,mes_referencia,consistencia_anual,margem_bruta_litro_anual"
    )
    mapa_anual = {}
    for a in raw_anual:
        cod_a = str(a.get("codigo_lr") or "").strip().upper()
        mes_a = str(a.get("mes_referencia") or "")[:7]
        if cod_a and mes_a and a.get("margem_bruta_litro_anual") is not None:
            mapa_anual[(cod_a, mes_a)] = a
    if not mapa_anual:
        print("❌ Nenhuma janela anual com margem em elabore_raw_consistencia_anual. "
              "Rode o SQL 2026_09_30_add_indicadores_anuais_elabore_raw.sql e depois "
              "scripts/functions/atualizar_detalhamento_consistencia.py.")
        return 0
    print(f"📅 Janelas anuais com margem disponíveis: {len(mapa_anual)}")
    sem_anual = 0

    # 2. Localizar e ler o relatório de indicadores MENSAIS do Elabore (fonte única da fato econômica)
    dir_elabore_mensal = Path(config.get("caminhos", {}).get("elabore_mensal", ""))
    fallbacks = [
        raiz / "data" / "input",
        raiz / "DATA" / "INPUT",
        raiz / "data" / "input" / "temp",
        raiz / "DATA" / "INPUT" / "TEMP",
    ]

    try:
        arquivo_excel = localizar_arquivo_recente(dir_elabore_mensal, fallbacks, "*indicadores_mensais.xlsx")
        df_excel = ler_excel_seguro(arquivo_excel, sheet_name="Indicadores Mensais")
        print(f"📊 Planilha mensal carregada com sucesso ({len(df_excel)} linhas e {len(df_excel.columns)} colunas).")
    except Exception as e:
        print(f"⚠️ Erro ao localizar/ler planilha de indicadores mensais: {e}")
        return 0

    if df_excel.empty:
        print("⚠️ Planilha de indicadores mensais do Elabore está vazia.")
        return 0

    cols = list(df_excel.columns)

    # Identificar colunas essenciais (chave)
    col_codigo = encontrar_nome_coluna(cols, ["código lr", "codigo lr", "labor_rural_code", "codigo_lr", "código", "codigo"])
    col_mes = encontrar_nome_coluna(cols, ["mês de referência", "mes de referencia", "reference_month", "referência", "referencia", "mes_referencia"])
    col_idfaz = encontrar_nome_coluna(cols, ["idfazenda", "id_property", "código fazenda", "codigo fazenda"])

    if not col_codigo or not col_mes:
        print("❌ Colunas obrigatórias ('Código LR', 'Mês de Referência') não foram encontradas na planilha.")
        return 0

    # Colunas descritivas (fuzzy é aceitável aqui — não são valores numéricos de custo/receita)
    col_produtor = encontrar_nome_coluna(cols, ["fazenda - produtor", "property_entrepreneur_label", "nome produtor", "produtor", "fazenda"])
    col_consultor = encontrar_nome_coluna(cols, ["consultor", "grupo de atendimento", "consultor_campo", "consultor de campo"])
    col_agro = encontrar_nome_coluna(cols, ["agroindústria", "agroindustria", "agroindustry_name"])

    # Resolver colunas financeiras/zootécnicas por correspondência EXATA (nunca substring)
    resolvidas_base = {k: resolver_coluna_exata(cols, v) for k, v in MAPA_COLUNAS_BASE.items()}
    resolvidas_coe = {k: resolver_coluna_exata(cols, v) for k, v in MAPA_COLUNAS_COE.items()}

    faltantes = [k for k, v in {**resolvidas_base, **resolvidas_coe}.items() if v is None]
    if faltantes:
        print(f"⚠️ Aviso: colunas não encontradas na planilha (tratadas como 0): {faltantes}")

    def num(row: pd.Series, chave_resolvida: dict, chave: str) -> float:
        col = chave_resolvida.get(chave)
        return converter_numero_br_float(row.get(col)) if col else 0.0

    agora_iso = datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat()
    registros_dict = {}

    for idx, row in df_excel.iterrows():
        raw_cod = row.get(col_codigo)
        if pd.isna(raw_cod) or str(raw_cod).strip() == "":
            continue
        cod_lr = str(raw_cod).strip().upper()

        raw_mes = row.get(col_mes)
        if pd.isna(raw_mes):
            continue

        try:
            dt_mes = pd.to_datetime(raw_mes)
            mes_str = dt_mes.strftime("%Y-%m-01")
        except Exception:
            continue

        id_comp = f"{cod_lr}_{mes_str}"

        janela_anual = mapa_anual.get((cod_lr, mes_str[:7]))
        if janela_anual is None:
            sem_anual += 1
            continue

        # Fallback para metadados via sq_raw_vinculos (fonte de verdade de projeto/consultor)
        meta_vinculo = mapa_vinculos.get(cod_lr, {})

        raw_produtor = row.get(col_produtor) if col_produtor and pd.notna(row.get(col_produtor)) else None
        if raw_produtor and " - " in str(raw_produtor):
            # Coluna "Fazenda - Produtor" vem composta; mantemos apenas o nome do produtor (após o PRIMEIRO hífen)
            nome_prod = str(raw_produtor).split(" - ", 1)[1].strip()
        else:
            nome_prod = raw_produtor if raw_produtor else meta_vinculo.get("nome_produtor")

        nome_cons = row.get(col_consultor) if col_consultor and pd.notna(row.get(col_consultor)) else (
            meta_vinculo.get("consultor_grupo_atendimento") or meta_vinculo.get("grupo_atendimento") or meta_vinculo.get("consultor_campo")
        )
        agro = row.get(col_agro) if col_agro and pd.notna(row.get(col_agro)) else (
            meta_vinculo.get("codigo_agroindustria") or meta_vinculo.get("agroindustria")
        )
        # Projeto vem exclusivamente do cadastro de vínculos: "Filtro 1/2" do Elabore é posse
        # da terra (própria/arrendada), não projeto de consultoria.
        proj = meta_vinculo.get("projeto")
        reg = meta_vinculo.get("unidade_atendimento") or meta_vinculo.get("regiao") or meta_vinculo.get("estado_produtor")

        # IdFazenda no relatório Elabore é UUID (ex: "00220277-a58e-4b9e-9b89-e6acf8a1a400")
        raw_idfaz = row.get(col_idfaz) if col_idfaz and pd.notna(row.get(col_idfaz)) else None
        id_prop_elabore = str(raw_idfaz).strip() if raw_idfaz and str(raw_idfaz).strip() != "" else None
        id_faz = str(meta_vinculo.get("codigo_fazenda") or meta_vinculo.get("idfazenda") or "").strip() or None

        dt_assoc = meta_vinculo.get("data_associacao")
        dt_assoc_str = str(dt_assoc)[:10] if pd.notna(dt_assoc) and str(dt_assoc).strip() else None

        # --- Zootécnico ---
        vol_produzido = num(row, resolvidas_base, "producao_total")
        vol_vendido = num(row, resolvidas_base, "volume_vendido")
        dias_no_mes = int(num(row, resolvidas_base, "dias_no_mes")) or calendar.monthrange(dt_mes.year, dt_mes.month)[1]

        vol_diario_raw = row.get(resolvidas_base.get("producao_diaria")) if resolvidas_base.get("producao_diaria") else None
        vol_diario = converter_numero_br_float(vol_diario_raw) if pd.notna(vol_diario_raw) else (
            round(vol_produzido / dias_no_mes, 2) if dias_no_mes > 0 else 0.0
        )

        vl = num(row, resolvidas_base, "vacas_lactacao")
        vt = num(row, resolvidas_base, "vacas_totais")

        produtividade = num(row, resolvidas_base, "produtividade")
        if produtividade == 0.0 and vl > 0 and vol_produzido > 0 and dias_no_mes > 0:
            produtividade = round(vol_produzido / (vl * dias_no_mes), 2)

        # --- Financeiro: Receita ---
        receita_venda = num(row, resolvidas_base, "receita_venda_leite")
        receita_total_leite = num(row, resolvidas_base, "receita_total_leite")
        if receita_total_leite == 0.0:
            receita_total_leite = receita_venda
        receita_bruta_atividade = num(row, resolvidas_base, "receita_bruta_atividade")
        if receita_bruta_atividade == 0.0:
            receita_bruta_atividade = receita_total_leite

        preco_litro = round(receita_venda / vol_vendido, 4) if vol_vendido > 0 else 0.0

        # --- Financeiro: COE (fórmula validada 1:1 contra o Elabore Anual) ---
        coe_conc = num(row, resolvidas_coe, "concentrado_mineral")
        coe_vol = num(row, resolvidas_coe, "volumoso") + num(row, resolvidas_coe, "forrageira_propria_volumoso")
        coe_moc = num(row, resolvidas_coe, "mo_contratada")
        coe_mof = num(row, resolvidas_coe, "mo_familiar")  # NÃO entra no COE — registrado à parte
        coe_san = (
            num(row, resolvidas_coe, "medicamentos_vacinas")
            + num(row, resolvidas_coe, "hormonios")
            + num(row, resolvidas_coe, "reproducao")
        )
        coe_out = (
            num(row, resolvidas_coe, "acessorios_gerais")
            + num(row, resolvidas_coe, "despesas_administrativas")
            + num(row, resolvidas_coe, "arrendamento")
            + num(row, resolvidas_coe, "assistencia_tecnica")
            + num(row, resolvidas_coe, "reparos_consertos")
            + num(row, resolvidas_coe, "impostos_taxas")
            + num(row, resolvidas_coe, "energia_eletrica")
            + num(row, resolvidas_coe, "combustivel")
            + num(row, resolvidas_coe, "leite_bezerras")
            + num(row, resolvidas_coe, "sucedaneo")
            + num(row, resolvidas_coe, "reposicao_cama")
        )
        coe_total = coe_conc + coe_vol + coe_moc + coe_san + coe_out

        coe_litro = round(coe_total / vol_produzido, 4) if vol_produzido > 0 else 0.0

        # Margem bruta = a da janela anual de 12 meses do Elabore (gross_margin_liter_annual), não
        # Receita Bruta da Atividade - COE do mês: a receita mensal inclui venda de animais e distorce
        # o R$/L de quem descartou rebanho. margem_bruta_total fica em "equivalente mensal"
        # (margem anual/L x litros do mês) para a API continuar calculando soma(MB)/soma(litros).
        mb_litro = round(float(janela_anual["margem_bruta_litro_anual"]), 4)
        mb_total = round(mb_litro * vol_produzido, 4)
        flag_mb_pos = 1 if mb_litro > 0 else 0

        # --- Qualidade do dado: só considerar "com dados econômicos" quando a linha tem despesas lançadas ---
        possui_despesas = int(num(row, resolvidas_base, "possui_dados_despesas"))
        possui_receita = int(num(row, resolvidas_base, "possui_dados_receita"))
        possui_dados_econ = 1 if (possui_despesas == 1 or coe_total > 0) and (possui_receita == 1 or receita_bruta_atividade > 0) else 0

        # Status da janela anual (CONSISTENTE / INCONSISTENTE / OUTLIER): é ele que o dashboard usa
        # em status_consistencia_mensal para decidir quem entra nos gráficos.
        status_consistencia = str(janela_anual.get("consistencia_anual") or "").strip() or None

        registros_dict[id_comp] = {
            "id_composto": id_comp,
            "codigo_lr": cod_lr,
            "idfazenda": id_faz,
            "id_propriedade_elabore": id_prop_elabore,
            "nome_produtor": normalizar_texto_maiusculo(nome_prod),
            "nome_consultor": normalizar_texto_maiusculo(nome_cons),
            "projeto": str(proj).strip() if pd.notna(proj) else None,
            "agroindustria": str(agro).strip() if pd.notna(agro) else None,
            "regiao": str(reg).strip() if pd.notna(reg) else None,
            "mes_referencia": mes_str,
            "volume_leite_mes": vol_produzido,
            "volume_leite_vendido": vol_vendido,
            "volume_diario_litros": vol_diario,
            "vacas_lactacao": vl,
            "vacas_totais": vt,
            "produtividade_l_vl_dia": produtividade,
            "receita_leite_total": receita_total_leite,
            "receita_bruta_atividade": receita_bruta_atividade,
            "preco_medio_litro": preco_litro,
            "coe_total_reais": coe_total,
            "coe_por_litro": coe_litro,
            "margem_bruta_total": mb_total,
            "margem_bruta_por_litro": mb_litro,
            "flag_mb_positiva": flag_mb_pos,
            "coe_concentrado": coe_conc,
            "coe_volumoso": coe_vol,
            "coe_mao_de_obra": coe_moc,
            "custo_mo_familiar": coe_mof,
            "coe_sanidade": coe_san,
            "coe_outros": coe_out,
            "possui_dados_economicos": possui_dados_econ,
            "status_consistencia_mensal": status_consistencia,
            "data_associacao": dt_assoc_str,
            "data_processamento": agora_iso
        }

    if not registros_dict:
        print("⚠️ Nenhum registro fato econômico foi extraído.")
        return 0

    records = list(registros_dict.values())
    print(f"📊 Total de registros econômicos extraídos da planilha Elabore: {len(records)} "
          f"({sem_anual} fazenda x mês da planilha mensal ignorados por não terem janela anual)")

    records = sanitize_json_records(records)
    chunk_size = config.get("execucao", {}).get("chunk_size_upsert", 1000)
    sucessos = 0

    print("🚀 Enviando registros para o Supabase (tab_fato_economico)...")
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        try:
            supabase.table("tab_fato_economico").upsert(
                chunk, on_conflict="id_composto"
            ).execute()
            sucessos += len(chunk)
            print(f"   ✅ Lote {i // chunk_size + 1}: {len(chunk)} registros inseridos/atualizados.")
        except Exception as e:
            print(f"   ⚠️ Aviso no lote {i // chunk_size + 1}: {e}")

    print(f"✨ Concluído! {sucessos} registros processados para 'tab_fato_economico'.")

    # Remove da fato quem não tem janela anual (linhas de cargas anteriores, como fazendas com
    # menos de 12 meses): toda linha gravada agora carrega data_processamento = agora_iso.
    if sucessos == len(records):
        try:
            removidas = supabase.table("tab_fato_economico").delete().lt("data_processamento", agora_iso).execute()
            print(f"🧹 Linhas sem janela anual removidas da fato: {len(removidas.data or [])}")
        except Exception as e:
            print(f"⚠️ Não foi possível limpar linhas antigas de tab_fato_economico: {e}")
    else:
        print("⚠️ Houve lotes com erro: limpeza de linhas antigas da fato não executada.")
    return sucessos


if __name__ == "__main__":
    processar_e_carregar_fato_economico()
