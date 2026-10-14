# -*- coding: utf-8 -*-
"""
Script: carregar_historico_visitas.py
Objetivo: Carregar a camada raw de visitas (sq_raw_visitas) a partir das exportações
          LISTA_GERAL do SmartQuestion, em duas etapas com o mesmo layout (aba 'BD'):

  1. Base estática congelada  -> BD_SMARTQUESTION/BACKUPS/VISITAS/<visitas_historico_arquivos>
                                 somente data_visita <= referencia.data_corte_estatica_visitas
  2. Base dinâmica            -> BD_SMARTQUESTION/LISTA_GERAL_VISITAS.xlsx
                                 somente data_visita >= referencia.data_inicial_fato_visitas

Grava apenas a raw (upsert por id_composto = sha256("ATEND_<id_atendimento>")). As colunas
exclusivas dos relatórios por projeto (valores pagos, id_farm, dados zootécnicos) são
preservadas, pois o upsert só atualiza as colunas enviadas. A fato de visitas é publicada
depois por camada_consumo.py.

Ao final confere se todos os atendimentos dos arquivos estão no Supabase e falha se faltar
algum — a raw precisa espelhar os arquivos por completo.

Uso:
  python scripts/functions/carregar_historico_visitas.py
"""
from __future__ import annotations

import hashlib
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

caminho_atual = Path(__file__).resolve()
for candidato in [caminho_atual, *caminho_atual.parents]:
    if (candidato / "scripts").is_dir() and ((candidato / "data").is_dir() or (candidato / "app").is_dir()):
        raiz_projeto = candidato
        break
else:
    raiz_projeto = caminho_atual.parents[2]
for p in [raiz_projeto, raiz_projeto / "scripts", raiz_projeto / "scripts" / "functions"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from functions.function import carregar_env, obter_cliente_supabase  # noqa: E402
from functions import regras_negocio as rn  # noqa: E402

TABELA_RAW = "sq_raw_visitas"
LOTE = 1000

MAPA_COLUNAS = {
    "Código do atendimento": "id_atendimento",
    "Código do(a) produtor(a)": "codigo_lr",
    "Produtor(a)": "nome_produtor",
    "Propriedade": "nome_propriedade",
    "Consultor(a)": "nome_consultor",
    "Data da visita": "data_visita",
    "Tipo de visita": "tipo_visita",
    "Projeto": "projeto",
}
COLUNAS_OPCIONAIS = {"Propriedade"}
CAMPOS_ZOOTECNICOS = [
    "area_pecuaria_ha", "mdo_dias_homem", "producao_l_dia", "ccs_mensal", "cpp_mensal",
    "gordura_mensal", "proteina_mensal", "vacas_lactacao", "vacas_secas",
    "bezerras_aleitamento", "bezerros_aleitamento", "novilhas", "reprodutores",
    "receptoras", "rebanho_total",
]
COLUNAS_RAW = ["id_composto", "id_atendimento", "codigo_lr", "nome_produtor", "nome_propriedade", "nome_consultor",
               "data_visita", "tipo_visita", "projeto", "id_farm", "valor_pago_produtor",
               "valor_pago_agroindustria", *CAMPOS_ZOOTECNICOS, "origem_dados", "data_processamento"]

COLUNAS_INTEIRAS = {
    "id_atendimento", "vacas_lactacao", "vacas_secas", "bezerras_aleitamento",
    "bezerros_aleitamento", "novilhas", "reprodutores", "receptoras", "rebanho_total",
}

# Relatórios específicos por agroindústria: cada um usa um formulário próprio do SmartQuestion
# (fora do padrão de aba "BD" da LISTA_GERAL_VISITAS.xlsx) e traz valor_pago_produtor/
# valor_pago_agroindustria/id_farm reais da visita, que a LISTA_GERAL não tem. Por isso têm
# prioridade: quando o mesmo id_atendimento existe nos dois, o registro do relatório específico
# substitui o da LISTA_GERAL por inteiro. Mapeamento por posição de coluna (0-indexado) porque os
# cabeçalhos desses arquivos têm espaços/quebras de linha inconsistentes entre exportações.
RELATORIOS_ESPECIFICOS = {
    "LISTA_ALVOAR_VISITA.xlsx": dict(
        sheet="INF_GERAIS", header_row=2,  # cabeçalho na linha 3 do Excel
        projeto="ALVOAR", tipo_visita="ALVOAR ASSIST",
        colunas={"nome_produtor": 0, "codigo_lr": 1, "nome_propriedade": 2, "nome_consultor": 3,
                 "id_atendimento": 4, "data_visita": 6,
                 "area_pecuaria_ha": 8, "mdo_dias_homem": 9, "producao_l_dia": 10,
                 "ccs_mensal": 11, "cpp_mensal": 12, "gordura_mensal": 13, "proteina_mensal": 14,
                 "vacas_lactacao": 17, "vacas_secas": 18, "bezerras_aleitamento": 19,
                 "bezerros_aleitamento": 20, "novilhas": 21, "reprodutores": 22, "receptoras": 23,
                 "rebanho_total": 24,
                 "valor_pago_produtor": 31, "valor_pago_agroindustria": 32},
    ),
    "LISTA_LPA_VISITA.xlsx": dict(
        # Mesmo layout do ALVOAR (o próprio arquivo exportado traz o cabeçalho "...VISITA ALVOAR"
        # por erro de template no SmartQuestion — vale confirmar com quem exporta).
        sheet="INF_GERAIS", header_row=2,
        projeto="LPA", tipo_visita="RELATORIO DE VISITA LPA",
        colunas={"nome_produtor": 0, "codigo_lr": 1, "nome_propriedade": 2, "nome_consultor": 3,
                 "id_atendimento": 4, "data_visita": 6,
                 "area_pecuaria_ha": 8, "mdo_dias_homem": 9, "producao_l_dia": 10,
                 "ccs_mensal": 11, "cpp_mensal": 12, "gordura_mensal": 13, "proteina_mensal": 14,
                 "vacas_lactacao": 17, "vacas_secas": 18, "bezerras_aleitamento": 19,
                 "bezerros_aleitamento": 20, "novilhas": 21, "reprodutores": 22, "receptoras": 23,
                 "rebanho_total": 24,
                 "valor_pago_produtor": 31, "valor_pago_agroindustria": 32},
    ),
    "LISTA_CCPR_VISITA.xlsx": dict(
        # DADOS_COLETADOS do CCPR não tem coluna de data; por isso a base vem de DADOS_DA_VISITA
        # (cabeçalho na linha 5 do Excel), que tem id/nome/consultor/propriedade/data completos.
        # Os campos zootécnicos vêm de um merge com DADOS_COLETADOS por id_atendimento (esse
        # arquivo não tem bezerras_aleitamento nem receptoras — ficam nulos).
        sheet="DADOS_DA_VISITA", header_row=4,
        projeto="CCPR", tipo_visita="RELATORIO DE VISITA ATEG/CCPR_GOIAS",
        # Na exportação de 09/2026 as colunas 7 ("Data fim no sistema"), 8 ("Data da realização da visita")
        # e 11 vêm vazias nas 334 linhas; os valores reais estão repetidos nas colunas 34–36. Por isso a data
        # vem da 36 e a 8 fica como alternativa, caso o SmartQuestion volte ao layout anterior.
        colunas={"id_atendimento": 1, "nome_consultor": 2, "codigo_lr": 3, "nome_produtor": 4,
                 "nome_propriedade": 5, "data_visita": 36},
        colunas_alternativas={"data_visita": 8},
        sheet_extra="DADOS_COLETADOS", header_row_extra=3,
        colunas_extra={"id_atendimento": 1,
                        "area_pecuaria_ha": 7, "mdo_dias_homem": 8, "producao_l_dia": 9,
                        "vacas_lactacao": 10, "vacas_secas": 11, "bezerros_aleitamento": 12,
                        "novilhas": 13, "reprodutores": 14, "rebanho_total": 15,
                        "ccs_mensal": 22, "cpp_mensal": 24, "gordura_mensal": 26,
                        "proteina_mensal": 27},
    ),
    "LISTA_REGENERA_VISITA.xlsx": dict(
        sheet="DADOS_COLETADOS", header_row=3,
        projeto="REGENERA", tipo_visita="RELATORIO DE VISITA REGENERA",
        colunas={"id_atendimento": 1, "nome_consultor": 2, "codigo_lr": 3, "id_farm": 4,
                 "nome_produtor": 5, "nome_propriedade": 6, "data_visita": 9,
                 "area_pecuaria_ha": 11, "mdo_dias_homem": 12, "producao_l_dia": 13,
                 "vacas_lactacao": 14, "vacas_secas": 15, "bezerras_aleitamento": 16,
                 "bezerros_aleitamento": 17, "novilhas": 18, "reprodutores": 19,
                 "receptoras": 20, "rebanho_total": 21,
                 "ccs_mensal": 28, "cpp_mensal": 30, "gordura_mensal": 32, "proteina_mensal": 33},
    ),
    "LISTA_SEMEAR_VISITA.xlsx": dict(
        sheet="DADOS_COLETADOS", header_row=3,
        projeto="SEMEAR", tipo_visita="RELATÓRIO DE VISITA SEMEAR - V2",
        colunas={"id_atendimento": 1, "nome_consultor": 2, "codigo_lr": 3, "nome_produtor": 4,
                 "nome_propriedade": 5, "data_visita": 7,
                 "area_pecuaria_ha": 8, "mdo_dias_homem": 9, "producao_l_dia": 10,
                 "vacas_lactacao": 11, "vacas_secas": 12, "bezerras_aleitamento": 13,
                 "bezerros_aleitamento": 14, "novilhas": 15, "reprodutores": 16,
                 "receptoras": 17, "rebanho_total": 18,
                 "ccs_mensal": 24, "cpp_mensal": 26, "gordura_mensal": 28, "proteina_mensal": 29},
    ),
}


def carregar_configuracao(raiz: Path) -> dict:
    from functions.config_projeto import ler_config
    return ler_config(raiz)


def ler_lista_geral(caminho: Path, origem: str) -> pd.DataFrame:
    df = pd.read_excel(caminho, sheet_name="BD")
    if "Propriedade:" in df.columns and "Propriedade" not in df.columns:
        df = df.rename(columns={"Propriedade:": "Propriedade"})
    # As exportações atuais da LISTA_GERAL não trazem mais "Propriedade"; o nome vem dos
    # relatórios específicos e de tab_dim_fazendas_ativas (camada_consumo).
    faltando = [c for c in MAPA_COLUNAS if c not in df.columns and c not in COLUNAS_OPCIONAIS]
    if faltando:
        raise ValueError(f"{caminho.name}: colunas ausentes {faltando}")
    for c in COLUNAS_OPCIONAIS:
        if c not in df.columns:
            df[c] = None
    df = df[list(MAPA_COLUNAS)].rename(columns=MAPA_COLUNAS)
    df["origem_dados"] = origem
    df["arquivo"] = caminho.name
    return df


def ler_relatorio_especifico(caminho: Path, layout: dict) -> pd.DataFrame:
    """Lê um relatório específico de agroindústria (layout próprio, fora do padrão BD)."""
    bruto = pd.read_excel(caminho, sheet_name=layout["sheet"], header=None,
                           skiprows=layout["header_row"] + 1)
    df = pd.DataFrame({campo: bruto.iloc[:, idx] for campo, idx in layout["colunas"].items()})
    for campo, idx in layout.get("colunas_alternativas", {}).items():
        if idx < bruto.shape[1]:
            df[campo] = df[campo].where(df[campo].notna(), bruto.iloc[:, idx])

    if "sheet_extra" in layout:
        # Campos que não estão na aba principal (ex.: zootécnicos do CCPR): merge por
        # id_atendimento, à parte — se não casar, só o campo extra fica nulo, a visita não some.
        aux = pd.read_excel(caminho, sheet_name=layout["sheet_extra"], header=None,
                             skiprows=layout["header_row_extra"] + 1)
        extra = pd.DataFrame({campo: aux.iloc[:, idx] for campo, idx in layout["colunas_extra"].items()})
        df["_id_join"] = df["id_atendimento"].map(rn.limpar_id_atendimento)
        extra["_id_join"] = extra["id_atendimento"].map(rn.limpar_id_atendimento)
        extra = extra.drop(columns=["id_atendimento"]).dropna(subset=["_id_join"]).drop_duplicates(subset=["_id_join"], keep="last")
        df = df.merge(extra, on="_id_join", how="left").drop(columns=["_id_join"])

    df["projeto"] = layout["projeto"]
    df["tipo_visita"] = layout["tipo_visita"]
    df["origem_dados"] = caminho.stem
    df["arquivo"] = caminho.name
    for col in ("id_farm", "valor_pago_produtor", "valor_pago_agroindustria", *CAMPOS_ZOOTECNICOS):
        if col not in df.columns:
            df[col] = None
    return df


def montar_relatorios_especificos(cfg: dict) -> pd.DataFrame:
    """Consolida os relatórios específicos por agroindústria já normalizados e padronizados."""
    pasta_bd = Path(cfg["caminhos"]["bd_smartquestion"])
    partes = []
    for nome, layout in RELATORIOS_ESPECIFICOS.items():
        caminho = pasta_bd / nome
        if not caminho.exists():
            print(f"   ⚠️ Relatório específico não encontrado (ignorado): {caminho}")
            continue
        bruto = ler_relatorio_especifico(caminho, layout)
        df = padronizar(bruto)
        descartadas = len(bruto) - len(df)
        aviso = f" ({descartadas} linhas sem id_atendimento/data_visita válidos descartadas)" if descartadas else ""
        print(f"   -> {nome}: {len(df)} visitas ({layout['projeto']}){aviso}")
        partes.append(df)
    if not partes:
        return pd.DataFrame()
    especificos = pd.concat([p for p in partes if not p.empty], ignore_index=True)
    antes = len(especificos)
    especificos = especificos.drop_duplicates(subset=["id_atendimento"], keep="last")
    if antes != len(especificos):
        print(f"   ⚠️ {antes - len(especificos)} id_atendimento duplicados entre relatórios específicos "
              f"(mantido o último). Verifique se o mesmo atendimento não pertence a duas agroindústrias.")
    return especificos


def padronizar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["id_atendimento"] = df["id_atendimento"].map(rn.limpar_id_atendimento)
    # dayfirst=True: alguns relatórios específicos gravam a data como texto "DD/MM/AAAA"
    # (não como data real do Excel); sem isso, "15/01/2025" falha o parse (mês inválido) e
    # vira NaT, descartando a visita inteira no dropna abaixo.
    df["data_visita"] = pd.to_datetime(df["data_visita"], errors="coerce", dayfirst=True)
    df["codigo_lr"] = df["codigo_lr"].map(rn.limpar_codigo_lr).replace({"": None})
    df["nome_consultor"] = df["nome_consultor"].astype(str).str.strip().str.upper()
    df["nome_consultor"] = df["nome_consultor"].replace(rn.ALIAS_CONSULTORES).replace({"NAN": None, "NONE": None})
    return df.dropna(subset=["id_atendimento", "data_visita"])


def montar_base(raiz: Path, cfg: dict) -> pd.DataFrame:
    ref = cfg["referencia"]
    arquivos = cfg["smartquestion"]["arquivos"]
    pasta_bd = Path(cfg["caminhos"]["bd_smartquestion"])
    pasta_backup = pasta_bd / "BACKUPS" / "VISITAS"
    corte = pd.Timestamp(ref["data_corte_estatica_visitas"]) + pd.Timedelta(days=1)  # inclui o dia inteiro
    inicio_dinamico = pd.Timestamp(ref["data_inicial_fato_visitas"])
    if inicio_dinamico > corte:
        raise ValueError(f"Lacuna entre a base estática (até {ref['data_corte_estatica_visitas']}) "
                         f"e a dinâmica (a partir de {ref['data_inicial_fato_visitas']}).")

    print(f"📁 1. Base estática (até {ref['data_corte_estatica_visitas']}): {pasta_backup}")
    estaticas = []
    for nome in arquivos["visitas_historico_arquivos"]:
        caminho = pasta_backup / nome
        if not caminho.exists():
            raise FileNotFoundError(f"Arquivo da base estática não encontrado: {caminho}")
        df = padronizar(ler_lista_geral(caminho, "LISTA_GERAL_VISITAS_BACKUP"))
        df = df[df["data_visita"] < corte]
        print(f"   -> {nome}: {len(df)} visitas")
        estaticas.append(df)

    caminho_dinamico = pasta_bd / arquivos["visita_geral"]
    print(f"📁 2. Base dinâmica (a partir de {ref['data_inicial_fato_visitas']}): {caminho_dinamico.name}")
    dinamica = padronizar(ler_lista_geral(caminho_dinamico, "LISTA_GERAL_VISITAS"))
    dinamica = dinamica[dinamica["data_visita"] >= inicio_dinamico]
    print(f"   -> {len(dinamica)} visitas ({dinamica['data_visita'].min():%Y-%m-%d} a {dinamica['data_visita'].max():%Y-%m-%d})")

    # A base dinâmica prevalece se o mesmo atendimento aparecer nas duas.
    base = pd.concat([*estaticas, dinamica], ignore_index=True)
    base = base.drop_duplicates(subset=["id_atendimento"], keep="last")
    for col in ("id_farm", "valor_pago_produtor", "valor_pago_agroindustria", *CAMPOS_ZOOTECNICOS):
        base[col] = None

    print(f"\n📁 3. Relatórios específicos por agroindústria (prioridade sobre a LISTA_GERAL): {pasta_bd}")
    especificos = montar_relatorios_especificos(cfg)
    if not especificos.empty:
        ids_especificos = set(especificos["id_atendimento"])
        substituidas = int(base["id_atendimento"].isin(ids_especificos).sum())
        base = base[~base["id_atendimento"].isin(ids_especificos)]
        base = pd.concat([b for b in [base, especificos] if not b.empty], ignore_index=True)
        print(f"   -> {substituidas} atendimentos da LISTA_GERAL substituídos por dados dos relatórios "
              f"específicos; {len(especificos) - substituidas} atendimentos novos incorporados.")

    base["id_composto"] = base["id_atendimento"].map(lambda i: hashlib.sha256(f"ATEND_{i}".encode("utf-8")).hexdigest())
    base["data_processamento"] = datetime.now()
    return base


def registros_para_supabase(df: pd.DataFrame) -> list[dict]:
    registros = []
    for r in df[COLUNAS_RAW].to_dict(orient="records"):
        limpo = {}
        for k, v in r.items():
            if v is None or (isinstance(v, float) and math.isnan(v)) or (not isinstance(v, str) and pd.isna(v)):
                limpo[k] = None
            elif isinstance(v, str) and v.strip().lower() in ("nan", "none", "null", ""):
                limpo[k] = None
            elif k in COLUNAS_INTEIRAS:
                try:
                    limpo[k] = int(round(float(v)))
                except (ValueError, TypeError):
                    limpo[k] = None
            elif isinstance(v, (pd.Timestamp, datetime)):
                limpo[k] = pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%S")
            elif hasattr(v, "item"):
                limpo[k] = v.item()
            else:
                limpo[k] = str(v).strip() if isinstance(v, str) else v
        registros.append(limpo)
    return registros


def ids_no_supabase(supabase) -> set[int]:
    ids, inicio = set(), 0
    while True:
        dados = supabase.table(TABELA_RAW).select("id_atendimento").range(inicio, inicio + LOTE - 1).execute().data or []
        ids.update(int(d["id_atendimento"]) for d in dados if d.get("id_atendimento") is not None)
        if len(dados) < LOTE:
            return ids
        inicio += LOTE


def enviar_lote(supabase, lote: list[dict], tentativas: int = 5) -> None:
    """Upsert com retry: conexões longas com o Supabase caem de vez em quando (WinError 10054)."""
    for t in range(1, tentativas + 1):
        try:
            supabase.table(TABELA_RAW).upsert(lote, on_conflict="id_composto").execute()
            return
        except Exception as e:
            if t == tentativas:
                raise
            print(f"   ⚠️ Falha no lote ({e}); tentativa {t}/{tentativas}, aguardando...")
            time.sleep(2 * t)


def preservar_nome_propriedade(supabase, base: pd.DataFrame) -> None:
    """A LISTA_GERAL não traz mais a propriedade: reaproveita o nome já gravado na raw
    para que o upsert não o sobrescreva com nulo."""
    sem_nome = base["nome_propriedade"].isna() | (base["nome_propriedade"].astype(str).str.strip() == "")
    if not sem_nome.any():
        return
    existentes, inicio = {}, 0
    while True:
        dados = (supabase.table(TABELA_RAW).select("id_atendimento,nome_propriedade")
                 .range(inicio, inicio + LOTE - 1).execute().data or [])
        existentes.update({int(d["id_atendimento"]): d["nome_propriedade"] for d in dados
                           if d.get("id_atendimento") is not None and d.get("nome_propriedade")})
        if len(dados) < LOTE:
            break
        inicio += LOTE
    base.loc[sem_nome, "nome_propriedade"] = base.loc[sem_nome, "id_atendimento"].map(existentes)
    faltam = int(base["nome_propriedade"].isna().sum())
    print(f"   ℹ️ LISTA_GERAL sem coluna 'Propriedade': nome preservado da raw para "
          f"{int(sem_nome.sum()) - faltam} atendimentos; {faltam} ficam sem nome (camada de consumo usa tab_dim_fazendas_ativas).")


def executar_carga_historico_visitas(raiz: Path | None = None) -> pd.DataFrame:
    raiz = raiz or raiz_projeto
    print("=" * 70)
    print("🚀 CARGA DA RAW DE VISITAS (BASE ESTÁTICA + LISTA_GERAL_VISITAS DINÂMICA)")
    print("=" * 70)
    carregar_env(raiz)
    cfg = carregar_configuracao(raiz)
    base = montar_base(raiz, cfg)
    print(f"\n📊 Total consolidado: {len(base)} atendimentos únicos "
          f"({base['data_visita'].min():%Y-%m-%d} a {base['data_visita'].max():%Y-%m-%d})")

    supabase = obter_cliente_supabase(raiz)
    preservar_nome_propriedade(supabase, base)
    registros = registros_para_supabase(base)
    print(f"\n⬆️ Enviando {len(registros)} registros para {TABELA_RAW}...")
    for i in range(0, len(registros), LOTE):
        enviar_lote(supabase, registros[i:i + LOTE])
    print("   ✅ Upsert concluído.")

    faltando = set(base["id_atendimento"].astype(int)) - ids_no_supabase(supabase)
    if faltando:
        raise RuntimeError(f"{len(faltando)} atendimentos dos arquivos não estão em {TABELA_RAW} após a carga "
                           f"(ex.: {sorted(faltando)[:10]}). A raw não pode ser publicada incompleta.")
    print(f"   ✅ Conferência: todos os {len(base)} atendimentos dos arquivos estão em {TABELA_RAW}.")
    print("=" * 70)
    return base


if __name__ == "__main__":
    executar_carga_historico_visitas()
