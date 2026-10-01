# -*- coding: utf-8 -*-
"""
Camada de consumo do dashboard (etapa final do pipeline).

Lê as camadas raw/dim do Supabase e o Elabore (Azure PostgreSQL), aplica as
regras de `regras_negocio` e grava tabelas prontas para leitura direta pela API:

  tab_fato_visitas          1 linha por visita técnica de leite válida (id_atendimento)
  tab_fato_carteira_mensal  1 linha por fazenda ativa × mês × consultor de campo
  tab_fato_consistencia     1 linha por fazenda × mês de referência do Elabore
  tab_fato_movimentacao     entradas/saídas de leite enriquecidas (lê o bruto gravado em
                            sq_raw_movimentacao por reconciliar_movimentacao_e_ativos.py)
  tab_dim_consultor_atuacao ponte consultor × agroindústria × região × mês (relacionamento dos filtros);
                            também completa tab_dim_consultor e alinha tab_fato_economico às dimensões
  tab_fato_producao_mensal  1 linha por fazenda × mês com volume, produtividade e qualidade do leite (última visita
                            do mês com medição); marca e trata registros suspeitos (config.yaml: producao)
  tab_dim_fazendas_ativas   (colunas latitude, longitude, origem_coordenada, coordenada_corrigida) coordenada saneada
                            de cada fazenda: visitas do SmartQuestion/base do Procampo, Elabore ou posição aproximada
                            pelo município (mediana das outras fazendas da cidade)
  tab_fato_safra_forrageira fazenda × safra × cultura × ciclo × sistema de plantio (slide 7): área plantada e com
                            cobertura (bloco AREA) e área colhida, toneladas e custo nominal por etapa das produções
                            consistentes (bloco CUSTO), das raws elabore_raw_sistema_cobertura/elabore_raw_custo_forrageira

Região e agroindústria só saem das dimensões: uma região que não pertence à agroindústria da fazenda vira
"NÃO INFORMADA" e é listada em db/output/logs/*_divergencias_dimensoes.xlsx para correção na origem.

Todas as gravações são idempotentes: upsert pela chave + remoção das chaves que
deixaram de existir. A API não aplica nenhuma regra de negócio sobre esses dados.

Uso:
  python scripts/functions/camada_consumo.py            # grava no Supabase
  python scripts/functions/camada_consumo.py --dry-run  # só calcula e exporta auditoria
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

caminho_atual = Path(__file__).resolve()
for candidato in [caminho_atual, *caminho_atual.parents]:
    if (candidato / "scripts").is_dir() and ((candidato / "db").is_dir() or (candidato / "dashboard").is_dir()):
        raiz_projeto = candidato
        break
else:
    raiz_projeto = caminho_atual.parents[2]
for p in [raiz_projeto, raiz_projeto / "scripts", raiz_projeto / "scripts" / "functions"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from functions import regras_negocio as rn
except ImportError:
    import regras_negocio as rn

FUSO_SP = ZoneInfo("America/Sao_Paulo")
LOTE = 1000

TAB_VISITAS = "tab_fato_visitas"
TAB_CARTEIRA = "tab_fato_carteira_mensal"
TAB_CONSISTENCIA = "tab_fato_consistencia"
TAB_MOVIMENTACAO = "tab_fato_movimentacao"
TAB_MOVIMENTACAO_RAW = "sq_raw_movimentacao"
TAB_PRO_FAZENDAS = "pro_raw_fazendas"
TAB_PRO_VISITAS = "pro_raw_visitas"
TAB_PRO_MOVIMENTACAO = "pro_raw_movimentacao"
TAB_ECONOMICO = "tab_fato_economico"
TAB_DIM_CONSULTOR = "tab_dim_consultor"
TAB_CONSULTOR_ATUACAO = "tab_dim_consultor_atuacao"
TAB_PRODUCAO = "tab_fato_producao_mensal"
TAB_DIM_FAZENDAS = "tab_dim_fazendas_ativas"
TAB_SAFRA = "tab_fato_safra_forrageira"
TAB_SAFRA_CUSTO_RAW = "elabore_raw_custo_forrageira"
TAB_SAFRA_SISTEMA_RAW = "elabore_raw_sistema_cobertura"
COLS_RAW_SAFRA_CUSTO = ("id_production,codigo_lr,cultura,safra,ciclo,produto_colhido,categoria,area_colhida_ha,"
                        "quantidade_kg,custo_pre_plantio_nom,custo_plantio_nom,custo_tratos_nom,"
                        "custo_colheita_planta_inteira_nom,custo_colheita_grao_nom,custo_etapa_nao_informada_nom,"
                        "custo_total_nom,custo_total_defl,custo_pre_plantio_defl,custo_plantio_defl,custo_tratos_defl,"
                        "custo_colheita_planta_inteira_defl,custo_colheita_grao_defl,custo_etapa_nao_informada_defl,"
                        "sistema_plantio,sem_quantidade_produzida,ciclo_divergente,alerta_custo_ha")
COLS_RAW_SAFRA_SISTEMA = ("id_plantio,fonte,codigo_lr,cultura,safra,ciclo,area_plantada_ha,sistema_plantio,"
                          "cobertura_apos,sucessao_apos")

# Não deixa uma carga de raw incompleta esvaziar a fato: aborta se algum mês perder
# mais que esta fração das visitas já publicadas.
TOLERANCIA_QUEDA_VISITAS_MES = 0.30


# ─── Fontes ───────────────────────────────────────────────────────────────────

@dataclass
class Fontes:
    raw_visitas: pd.DataFrame
    dim_fazendas: pd.DataFrame
    raw_fazendas_grupo: pd.DataFrame
    raw_vinculos: pd.DataFrame
    raw_inativacoes: pd.DataFrame
    fato_movimentacao: pd.DataFrame
    raw_consistencia_mensal: pd.DataFrame
    raw_consistencia_anual: pd.DataFrame
    dim_consultor: pd.DataFrame
    dim_regiao: pd.DataFrame
    dim_regiao_alias: pd.DataFrame
    dim_agroindustria: pd.DataFrame
    elabore_propriedades: pd.DataFrame
    elabore_blocos: pd.DataFrame
    # Procampo (Piracanjuba fora do SmartQuestion): raws carregadas por carregar_procampo.py
    pro_fazendas: pd.DataFrame = field(default_factory=pd.DataFrame)
    pro_visitas: pd.DataFrame = field(default_factory=pd.DataFrame)
    pro_movimentacao: pd.DataFrame = field(default_factory=pd.DataFrame)
    cfg_procampo: dict = field(default_factory=dict)   # bloco `procampo:` do config.yaml
    cfg_producao: dict = field(default_factory=dict)   # bloco `producao:` do config.yaml
    # Safra (slide 7): raws carregadas por carregar_safra_forrageira.py
    safra_custo: pd.DataFrame = field(default_factory=pd.DataFrame)
    safra_sistema: pd.DataFrame = field(default_factory=pd.DataFrame)
    cfg_safra: dict = field(default_factory=dict)      # bloco `safra_forrageira:` do config.yaml


def _buscar_tudo(supabase, tabela: str, colunas: str = "*", ordem: str | None = None) -> pd.DataFrame:
    registros, inicio = [], 0
    while True:
        consulta = supabase.table(tabela).select(colunas)
        if ordem:
            consulta = consulta.order(ordem)   # sem order() o range() repete/perde linhas entre páginas
        dados = consulta.range(inicio, inicio + LOTE - 1).execute().data or []
        registros.extend(dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    return pd.DataFrame(registros)


SQL_ELABORE_PROPRIEDADES = """
SELECT upper(trim(labor_rural_code)) AS codigo_lr, dairy_region, agroindustry_name, property_status,
       upper(trim(uf)) AS uf, city, latitude, longitude
FROM analytics_mart.vw_dim_property
WHERE labor_rural_code IS NOT NULL AND trim(labor_rural_code) <> ''
"""

# Um bloco está preenchido quando a view correspondente tem algum valor > 0 no mês.
SQL_ELABORE_BLOCOS = """
SELECT upper(trim(p.labor_rural_code)) AS codigo_lr, b.reference_month::date AS mes_elabore,
       bool_or(b.receita) AS receita, bool_or(b.qualidade) AS qualidade, bool_or(b.alimentacao) AS alimentacao,
       bool_or(b.area) AS area, bool_or(b.rebanho) AS rebanho, bool_or(b.mdo) AS mdo,
       bool_or(b.energia) AS energia, bool_or(b.despesas) AS despesas
FROM (
  SELECT id_property, reference_month,
         (COALESCE(milk_sold_revenue,0)>0 OR COALESCE(milk_volume_sold,0)>0 OR COALESCE(other_revenues,0)>0 OR COALESCE(animal_sale,0)>0) AS receita,
         (ccs IS NOT NULL OR cpp IS NOT NULL OR fat IS NOT NULL OR protein IS NOT NULL) AS qualidade,
         false AS alimentacao, false AS area, false AS rebanho, false AS mdo, false AS energia, false AS despesas
  FROM analytics_mart.vw_revenue
  UNION ALL
  SELECT id_property, reference_month, false, false,
         (COALESCE(voluminous_consumed_quantity,0)>0 OR COALESCE(concentrate_consumed_quantity,0)>0 OR COALESCE(mineral_consumed_quantity,0)>0 OR COALESCE(voluminous_amount_total,0)>0 OR COALESCE(concentrate_amount_total,0)>0),
         false, false, false, false, false
  FROM analytics_mart.vw_feeding
  UNION ALL
  SELECT id_property, reference_month, false, false, false,
         (COALESCE(hectares_owned_forrageiras,0)>0 OR COALESCE(hectares_rented_forrageiras,0)>0 OR COALESCE(hectares_owned_app_reserva_legal,0)>0 OR COALESCE(hectares_owned_benfeitorias_estradas,0)>0),
         false, false, false, false
  FROM analytics_mart.vw_area_land_summary
  UNION ALL
  SELECT id_property, reference_month, false, false, false, false,
         (COALESCE(total_cows,0)>0 OR COALESCE(total_cattle,0)>0 OR COALESCE(lactating_cows,0)>0 OR COALESCE(dry_cows,0)>0),
         false, false, false
  FROM analytics_mart.vw_cattle
  UNION ALL
  SELECT id_property, reference_month, false, false, false, false, false,
         (COALESCE(family_labor_quantity,0)>0 OR COALESCE(hired_labor_quantity,0)>0 OR COALESCE(family_labor_expenses,0)>0 OR COALESCE(hired_labor_expenses,0)>0),
         false, false
  FROM analytics_mart.vw_labor
  UNION ALL
  SELECT id_property, reference_month, false, false, false, false, false, false,
         (COALESCE(energy,0)>0 OR COALESCE(fuel,0)>0),
         (COALESCE(general_expenses,0)>0 OR COALESCE(administration,0)>0 OR COALESCE(land_lease,0)>0 OR COALESCE(technical_assistance,0)>0 OR COALESCE(repairs,0)>0 OR COALESCE(medicines_vaccines,0)>0 OR COALESCE(reproduction,0)>0 OR COALESCE(milking_material,0)>0)
  FROM analytics_mart.vw_expense
) b
JOIN analytics_mart.vw_dim_property p ON p.id_property = b.id_property
WHERE p.labor_rural_code IS NOT NULL AND b.reference_month >= %(data_inicio)s
GROUP BY 1, 2
"""


def carregar_fontes(supabase, conexao_elabore, data_inicio_elabore: str) -> Fontes:
    print("📥 Lendo camadas raw/dim do Supabase...")
    t = {
        "raw_visitas": _buscar_tudo(supabase, "sq_raw_visitas",
                                    "id_atendimento,codigo_lr,nome_consultor,nome_produtor,nome_propriedade,data_visita,"
                                    "tipo_visita,projeto,origem_dados,id_farm,valor_pago_produtor,valor_pago_agroindustria,"
                                    "area_pecuaria_ha,mdo_dias_homem,producao_l_dia,ccs_mensal,cpp_mensal,gordura_mensal,"
                                    "proteina_mensal,vacas_lactacao,vacas_secas,bezerras_aleitamento,bezerros_aleitamento,"
                                    "novilhas,reprodutores,receptoras,rebanho_total,data_processamento"),
        "dim_fazendas": _buscar_tudo(supabase, "tab_dim_fazendas_ativas"),
        "raw_fazendas_grupo": _buscar_tudo(supabase, "sq_raw_fazendas_grupo",
                                           "codigo_produtor,grupo_ponto_atendimento,consultor"),
        "raw_vinculos": _buscar_tudo(supabase, "sq_raw_vinculos",
                                     "codigo_lr,nome_produtor,nome_propriedade,projeto,unidade_atendimento,"
                                     "tipo_ponto_atendimento,data_associacao,grupo_atendimento,consultor_grupo_atendimento,estado_produtor"),
        "raw_inativacoes": _buscar_tudo(supabase, "sq_raw_inativacoes_produtor",
                                        "codigo_lr,nome_produtor,projeto,grupo_ponto_atendimento,data_solicitacao,data_inativacao"),
        "fato_movimentacao": _buscar_tudo(supabase, TAB_MOVIMENTACAO_RAW,
                                          "id_composto,codigo_lr,nome_consultor,nome_produtor,numero_atendimento,data_movimentacao,"
                                          "movimentacao,motivo_inativacao,outro_motivo,data_solicitacao,data_processamento"),
        "raw_consistencia_mensal": _buscar_tudo(supabase, "elabore_raw_consistencia_mensal",
                                                "codigo_lr,mes_referencia,mes_elabore,consistencia_mensal,detalhamento_inconsistencia,nome_produtor"),
        "raw_consistencia_anual": _buscar_tudo(supabase, "elabore_raw_consistencia_anual",
                                               "codigo_lr,mes_referencia,consistencia_anual,detalhamento_inconsistencia"),
        "dim_consultor": _buscar_tudo(supabase, TAB_DIM_CONSULTOR, "id,nome_consultor,formacao_consultor,excluido,chave_consultor",
                                      ordem="id"),
        "dim_regiao": _buscar_tudo(supabase, "tab_dim_regiao", "id,id_agroindustria,nome_regiao,status", ordem="id"),
        "dim_regiao_alias": _buscar_tudo(supabase, "tab_dim_regiao_alias", "id,id_agroindustria,nome_bruto,nome_regiao,status",
                                         ordem="id"),
        "dim_agroindustria": _buscar_tudo(supabase, "tab_dim_agroindustria", "id,nome_agroindustria,nome_bi,exibir_no_bi",
                                          ordem="id"),
        "pro_fazendas": _buscar_tudo(supabase, TAB_PRO_FAZENDAS,
                                     "matricula,codigo_lr,situacao,nome_produtor,nome_propriedade,data_inicio,data_conclusao,"
                                     "uf,cidade,latitude,longitude",
                                     ordem="matricula"),
        "pro_visitas": _buscar_tudo(supabase, TAB_PRO_VISITAS,
                                    "id_composto,matricula,codigo_lr,nome_propriedade,nome_produtor,nome_consultor,tipo_visita,"
                                    "data_visita,rascunho,producao_l_dia,vacas_lactacao,ccs_mensal,cpp_mensal,gordura_mensal,"
                                    "proteina_mensal", ordem="id_composto"),
        "pro_movimentacao": _buscar_tudo(supabase, TAB_PRO_MOVIMENTACAO,
                                         "id_composto,matricula,codigo_lr,aba_origem,data_inicio,data_conclusao,consultor,"
                                         "status_movimentacao,motivo_saida", ordem="id_composto"),
    }
    # Raws da safra: enquanto a migration/carga não rodarem, a fato de safra só não é publicada.
    for nome, tabela, colunas, ordem in (
        ("safra_custo", TAB_SAFRA_CUSTO_RAW, COLS_RAW_SAFRA_CUSTO, "id_production"),
        ("safra_sistema", TAB_SAFRA_SISTEMA_RAW, COLS_RAW_SAFRA_SISTEMA, "id_plantio"),
    ):
        try:
            t[nome] = _buscar_tudo(supabase, tabela, colunas, ordem=ordem)
        except Exception as e:
            print(f"   ⚠️ {tabela} indisponível ({e}); a fato de safra não será publicada.")
            t[nome] = pd.DataFrame()
    for nome, df in t.items():
        print(f"   -> {nome}: {len(df)} linhas")

    print("📥 Lendo Elabore (analytics_mart)...")

    def consultar(sql: str, params: dict | None = None) -> pd.DataFrame:
        with conexao_elabore.cursor() as cur:
            cur.execute(sql, params)
            return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])

    t["elabore_propriedades"] = consultar(SQL_ELABORE_PROPRIEDADES)
    t["elabore_blocos"] = consultar(SQL_ELABORE_BLOCOS, {"data_inicio": data_inicio_elabore})
    print(f"   -> propriedades: {len(t['elabore_propriedades'])} | blocos propriedade×mês: {len(t['elabore_blocos'])}")
    return Fontes(**t)


# ─── Utilitários ──────────────────────────────────────────────────────────────

def _data(serie: pd.Series) -> pd.Series:
    """Converte para datetime naive (timestamps com fuso são normalizados em UTC)."""
    return pd.to_datetime(serie, errors="coerce", utc=True, format="mixed").dt.tz_localize(None)


def _mes(serie: pd.Series) -> pd.Series:
    return _data(serie).dt.to_period("M").dt.to_timestamp()


def _texto(valor, padrao=None):
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return padrao
    s = str(valor).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else padrao


def _numero(valor) -> float | None:
    """Converte para float preservando NULL (diferente de valor_pago_*, que usa 0.0 como padrão)."""
    if valor is None or pd.isna(valor):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor) -> int | None:
    """Converte para int preservando NULL. Para colunas INTEGER no Supabase."""
    if valor is None or pd.isna(valor):
        return None
    try:
        return int(round(float(valor)))
    except (TypeError, ValueError):
        return None


def _propriedade_valida(nome) -> str | None:
    s = _texto(nome)
    return s if s and rn.chave(s) not in ("PROPRIEDADE", "FAZENDA", "-") else None


def _iso_data(v):
    return None if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")


def _iso_ts(v):
    return None if v is None or pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%S")


def _hash_atendimento(id_atendimento: int) -> str:
    return hashlib.sha256(f"ATEND_{id_atendimento}".encode("utf-8")).hexdigest()


# ─── Contexto compartilhado (mapas de enriquecimento) ─────────────────────────

class Contexto:
    def __init__(self, f: Fontes, agora: datetime):
        self.agora = pd.Timestamp(agora).tz_localize(None) if pd.Timestamp(agora).tzinfo else pd.Timestamp(agora)
        dim_agro = f.dim_agroindustria.to_dict("records")
        self.regiao = rn.ResolvedorRegiao(f.dim_regiao.to_dict("records"), f.dim_regiao_alias.to_dict("records"), dim_agro)
        self.agro_dim = {rn.chave(a.get("nome_bi") or a.get("nome_agroindustria")): a for a in dim_agro}
        agro_por_id = {a["id"]: rn.chave(a.get("nome_bi") or a.get("nome_agroindustria")) for a in dim_agro}
        self.regiao_ids = {
            (agro_por_id[r["id_agroindustria"]], rn.chave(r["nome_regiao"])): int(r["id"])
            for r in f.dim_regiao.to_dict("records") if r.get("id_agroindustria") in agro_por_id
        }
        # (tipo, código, agroindústria, região) -> linha do relatório de divergências entre fatos e dimensões
        self.divergencias: dict[tuple, dict] = {}

        self.profissao: dict[str, str] = {}
        for r in f.dim_consultor.to_dict("records"):
            nome, formacao = _texto(r.get("nome_consultor")), _texto(r.get("formacao_consultor"))
            if nome and formacao:
                self.profissao.setdefault(rn.chave(nome), formacao)

        props = f.elabore_propriedades.copy()
        props["codigo_lr"] = props["codigo_lr"].map(rn.limpar_codigo_lr)
        aprovadas = props[props["property_status"] == "active_approved"]
        self.regiao_elabore = {
            r["codigo_lr"]: self.regiao.resolver(r["dairy_region"], rn.mapear_agroindustria(r["agroindustry_name"]))
            for r in aprovadas.dropna(subset=["dairy_region"]).to_dict("records")
        }
        self.regiao_elabore = {k: v for k, v in self.regiao_elabore.items() if v != rn.REGIAO_NAO_INFORMADA}
        # Agroindústria da fazenda segundo o Elabore: só entra quando a fazenda não tem agroindústria no SmartQuestion
        self.agro_elabore = {}
        for r in aprovadas.dropna(subset=["agroindustry_name"]).to_dict("records"):
            agro = rn.mapear_agroindustria(r["agroindustry_name"])
            if rn.chave(agro) in self.agro_dim and rn.chave(agro) != rn.chave(rn.AGRO_NAO_INFORMADA):
                self.agro_elabore[r["codigo_lr"]] = self.agro_dim[rn.chave(agro)].get("nome_bi") or agro
        codigos_consistencia = set(f.raw_consistencia_mensal["codigo_lr"].map(rn.limpar_codigo_lr))
        self.cadastrados_elabore = set(props["codigo_lr"]) | codigos_consistencia

        blocos = f.elabore_blocos.copy()
        blocos["codigo_lr"] = blocos["codigo_lr"].map(rn.limpar_codigo_lr)
        blocos["mes_elabore"] = _mes(blocos["mes_elabore"])
        self.blocos = {
            (r["codigo_lr"], r["mes_elabore"]): {b: bool(r[b]) for b in rn.BLOCOS_ELABORE}
            for r in blocos.to_dict("records")
        }

        # Fazenda de leite por mês e o registro mais recente de cada código
        dim = f.dim_fazendas.copy()
        dim = dim[dim["tipo_ponto_atendimento"].astype(str).str.upper() == "LEITE"].copy()
        dim["codigo_lr"] = dim["codigo_produtor"].map(rn.limpar_codigo_lr)
        dim["mes_referencia"] = _mes(dim["mes_referencia"])
        self.dim_leite = dim
        self.fazenda_mes = {(r["codigo_lr"], r["mes_referencia"]): r for r in dim.to_dict("records")}
        self.fazenda_recente = {r["codigo_lr"]: r for r in dim.sort_values("mes_referencia").to_dict("records")}

        vinc = f.raw_vinculos.copy()
        vinc["codigo_lr"] = vinc["codigo_lr"].map(rn.limpar_codigo_lr)
        vinc["data_associacao"] = _data(vinc["data_associacao"])
        self.data_associacao = vinc.dropna(subset=["data_associacao"]).groupby("codigo_lr")["data_associacao"].min().to_dict()
        vinc_leite = vinc[vinc["projeto"].map(rn.eh_cadeia_leite) & vinc["projeto"].notna()]
        self.vinculo_recente = {
            r["codigo_lr"]: r for r in vinc_leite.sort_values("data_associacao", na_position="first").to_dict("records")
        }
        self.vinculo_qualquer = {r["codigo_lr"]: r for r in vinc.sort_values("data_associacao", na_position="first").to_dict("records")}

        inat = f.raw_inativacoes.copy()
        inat["codigo_lr"] = inat["codigo_lr"].map(rn.limpar_codigo_lr)
        inat["data_efetiva"] = _data(inat["data_inativacao"]).fillna(_data(inat["data_solicitacao"]))
        inat = inat.dropna(subset=["data_efetiva"])
        self.inativacao_mais_recente = inat.groupby("codigo_lr")["data_efetiva"].max().to_dict()
        self.inativacao_meta = {r["codigo_lr"]: r for r in inat.to_dict("records")}

        mov = f.fato_movimentacao.copy()
        if not mov.empty:
            saidas = mov[mov["movimentacao"].astype(str).str.lower().str.contains("sa")].copy()
            saidas["codigo_lr"] = saidas["codigo_lr"].map(rn.limpar_codigo_lr)
            saidas["data_movimentacao"] = _data(saidas["data_movimentacao"])
            for cod, dt in saidas.groupby("codigo_lr")["data_movimentacao"].min().items():
                atual = self.inativacao_mais_recente.get(cod)
                if atual is None or pd.isna(atual):
                    self.inativacao_mais_recente[cod] = dt

        # Procampo: início na carteira = DATA.INÍCIO da base; fazenda que saiu é "inativada" na DATA.CONCLUSÃO.
        # Fazenda LR###### é do SmartQuestion e mantém os dados de lá.
        self.pro_cfg = f.cfg_procampo
        if not f.pro_fazendas.empty:
            pf = f.pro_fazendas.copy()
            pf["codigo_lr"] = pf["codigo_lr"].map(rn.limpar_codigo_lr)
            pf = pf[~pf["codigo_lr"].map(rn.eh_fazenda_smartquestion)]
            pf["data_inicio"] = _data(pf["data_inicio"])
            pf["data_conclusao"] = _data(pf["data_conclusao"])
            for r in pf.to_dict("records"):
                if pd.notna(r["data_inicio"]):
                    self.data_associacao.setdefault(r["codigo_lr"], r["data_inicio"])
                if r["situacao"] == "SAIU" and pd.notna(r["data_conclusao"]):
                    self.inativacao_mais_recente.setdefault(r["codigo_lr"], r["data_conclusao"])

        cm = f.raw_consistencia_mensal.copy()
        cm["codigo_lr"] = cm["codigo_lr"].map(rn.limpar_codigo_lr)
        cm["mes_referencia"] = _mes(cm["mes_referencia"])
        cm["mes_elabore"] = _mes(cm["mes_elabore"])
        cm["situacao"] = cm["consistencia_mensal"].map(rn.classificar_consistencia)
        self.consistencia_mensal = cm
        self.mensal = {(r["codigo_lr"], r["mes_referencia"]): r for r in cm.to_dict("records")}
        self.sequencia = self._calcular_sequencias(cm)

        ca = f.raw_consistencia_anual.copy()
        ca["codigo_lr"] = ca["codigo_lr"].map(rn.limpar_codigo_lr)
        ca["mes_referencia"] = _mes(ca["mes_referencia"])
        ca["situacao"] = ca["consistencia_anual"].map(rn.classificar_consistencia)
        self.anual = {(r["codigo_lr"], r["mes_referencia"]): r for r in ca.to_dict("records")}

    @staticmethod
    def _calcular_sequencias(cm: pd.DataFrame) -> dict:
        """Meses de calendário consecutivos com consistência mensal 'Consistente' até cada mês."""
        seq: dict = {}
        for cod, grupo in cm.sort_values("mes_referencia").groupby("codigo_lr"):
            atual, mes_anterior = 0, None
            for r in grupo.itertuples():
                continuo = mes_anterior is not None and r.mes_referencia == mes_anterior + pd.DateOffset(months=1)
                atual = (atual + 1 if continuo else 1) if r.situacao == rn.SITUACAO_CONSISTENTE else 0
                seq[(cod, r.mes_referencia)] = atual
                mes_anterior = r.mes_referencia
        return seq

    # Metadados cadastrais de uma fazenda (prioriza a carteira do mês)
    def fazenda(self, codigo_lr: str, mes=None) -> dict:
        if mes is not None and (codigo_lr, mes) in self.fazenda_mes:
            return self.fazenda_mes[(codigo_lr, mes)]
        return self.fazenda_recente.get(codigo_lr) or {}

    def projeto(self, codigo_lr: str, mes=None, fallback=None) -> str | None:
        faz = self.fazenda(codigo_lr, mes)
        return (_texto(faz.get("projeto")) or _texto(self.vinculo_recente.get(codigo_lr, {}).get("projeto"))
                or _texto(fallback))

    def _divergencia(self, tipo: str, codigo_lr: str, agroindustria, regiao, **extra) -> None:
        self.divergencias.setdefault((tipo, codigo_lr, agroindustria, regiao), {
            "tipo": tipo, "codigo_lr": codigo_lr, "agroindustria": agroindustria, "regiao": regiao, **extra})

    def agroindustria(self, codigo_lr: str, projeto, mes=None) -> str:
        """Agroindústria canônica (= tab_dim_agroindustria.nome_bi). Sem agroindústria no SmartQuestion, usa a
        do Elabore e, na falta dela, a agroindústria dona da região do Elabore (só quando a região tem um único dono)."""
        faz = self.fazenda(codigo_lr, mes)
        agro = rn.mapear_agroindustria(_texto(faz.get("agroindustria")) or projeto)
        if rn.chave(agro) == rn.chave(rn.AGRO_NAO_INFORMADA):
            inferida = self.agro_elabore.get(codigo_lr)
            if inferida:
                self._divergencia("AGRO_INFERIDA_DO_ELABORE", codigo_lr, inferida, self.regiao_elabore.get(codigo_lr))
                return inferida
            inferida = self._agro_pela_regiao(codigo_lr, faz)
            if inferida:
                self._divergencia("AGRO_INFERIDA_PELA_REGIAO", codigo_lr, inferida, _texto(faz.get("regiao")))
                return inferida
        elif rn.chave(agro) not in self.agro_dim:
            self._divergencia("AGRO_NAO_CADASTRADA", codigo_lr, agro, None)
        return agro

    def _agro_pela_regiao(self, codigo_lr: str, faz: dict) -> str | None:
        """Agroindústria dona da região da fazenda, só quando o nome da região é exatamente um nome canônico de
        tab_dim_regiao com um único dono (sigla ambígua como 'MG' ou 'Goiânia', que tem 2 donos, não infere)."""
        for regiao in (self.regiao_elabore.get(codigo_lr), _texto(faz.get("regiao"))):
            donos = self.regiao.agroindustrias_da(regiao) if regiao else set()
            if len(donos) == 1:
                dim = self.agro_dim.get(next(iter(donos)))
                if dim:
                    return dim.get("nome_bi") or dim.get("nome_agroindustria")
        return None

    def regiao_de(self, codigo_lr: str, agroindustria: str, projeto, mes=None) -> str:
        """Região canônica da fazenda, sempre uma região que a agroindústria possui em tab_dim_regiao.

        Candidatas, em ordem: Elabore e cadastro da fazenda. Região de outra agroindústria, ou que não está no
        cadastro, vira NÃO INFORMADA e entra no relatório de divergências, em vez de aparecer solta nos filtros."""
        faz = self.fazenda(codigo_lr, mes)
        candidatas = []
        if codigo_lr in self.regiao_elabore:
            candidatas.append(self.regiao_elabore[codigo_lr])
        candidatas.append(self.regiao.resolver(_texto(faz.get("regiao")) or _texto(faz.get("unidade_atendimento")),
                                               agroindustria, projeto))
        reais = [c for c in candidatas if rn.chave(c) != rn.chave(rn.REGIAO_NAO_INFORMADA)]
        for c in reais:
            if self.regiao.valida(agroindustria, c):
                return self.regiao.canonica[(rn.chave(agroindustria), rn.chave(c))]
        for c in reais:
            if rn.chave(agroindustria) == rn.chave(rn.AGRO_NAO_INFORMADA):
                tipo = "REGIAO_SEM_AGROINDUSTRIA"
            elif self.regiao.agroindustrias_da(c):
                tipo = "REGIAO_DE_OUTRA_AGROINDUSTRIA"
            else:
                tipo = "REGIAO_NAO_CADASTRADA"
            self._divergencia(tipo, codigo_lr, agroindustria, c, regiao_elabore=self.regiao_elabore.get(codigo_lr),
                              regiao_cadastro=_texto(faz.get("regiao")), unidade_atendimento=_texto(faz.get("unidade_atendimento")))
        return rn.REGIAO_NAO_INFORMADA

    def elabore(self, codigo_lr: str, mes_referencia) -> dict:
        """Cadastro e blocos do Elabore referentes aos dados do mês anterior ao de referência."""
        mes_elabore = mes_referencia - pd.DateOffset(months=1)
        blocos = self.blocos.get((codigo_lr, mes_elabore)) or {b: False for b in rn.BLOCOS_ELABORE}
        pct = round(sum(blocos.values()) / len(rn.BLOCOS_ELABORE) * 100)
        return {
            "cadastro_elabore": codigo_lr in self.cadastrados_elabore,
            "dados_elabore_pct": pct,
            "dados_elabore_status": rn.rotulo_dados_elabore(pct),
            "blocos_elabore": blocos,
        }

    def consistencia(self, codigo_lr: str, mes_referencia) -> dict:
        m = self.mensal.get((codigo_lr, mes_referencia)) or {}
        a = self.anual.get((codigo_lr, mes_referencia)) or {}
        return {
            "mes_elabore": m.get("mes_elabore"),
            "consistencia_mensal": m.get("situacao") or rn.SITUACAO_SEM_DADOS,
            "detalhamento_mensal": _texto(m.get("detalhamento_inconsistencia")),
            "consistencia_anual": a.get("situacao") or rn.SITUACAO_SEM_DADOS,
            "detalhamento_anual": _texto(a.get("detalhamento_inconsistencia")),
            "meses_sequenciais": self.sequencia.get((codigo_lr, mes_referencia), 0),
        }


# ─── tab_fato_visitas ──────────────────────────────────────────────────────────

def construir_fato_visitas(f: Fontes, ctx: Contexto) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (fato de visitas válidas, auditoria com o motivo de cada exclusão)."""
    v = f.raw_visitas.copy()
    v["id_atendimento"] = v["id_atendimento"].map(rn.limpar_id_atendimento)
    v["data_visita"] = _data(v["data_visita"])
    v["codigo_lr"] = v["codigo_lr"].map(rn.limpar_codigo_lr)
    v["_ordem"] = _data(v["data_processamento"])
    v = v.sort_values("_ordem", ascending=False).drop_duplicates(subset=["id_atendimento"], keep="first")
    v["mes_referencia"] = v["data_visita"].dt.to_period("M").dt.to_timestamp()

    linhas, auditoria = [], []
    for r in v.to_dict("records"):
        motivo = None
        consultores = rn.sanitizar_consultores(r.get("nome_consultor"))
        cod, mes = r["codigo_lr"], r["mes_referencia"]
        projeto = ctx.projeto(cod, mes, fallback=r.get("projeto")) if cod else _texto(r.get("projeto"))
        faz = ctx.fazenda(cod, mes) if cod else {}
        unidade = rn.chave(faz.get("unidade_atendimento") or ctx.vinculo_qualquer.get(cod, {}).get("unidade_atendimento"))

        if r["id_atendimento"] is None:
            motivo = "SEM_ID_ATENDIMENTO"
        elif pd.isna(r["data_visita"]):
            motivo = "SEM_DATA_VISITA"
        elif r["data_visita"] > ctx.agora:
            motivo = "DATA_VISITA_FUTURA"
        elif not cod or rn.eh_codigo_conta_consultor(cod):
            motivo = "SEM_CODIGO_PRODUTOR"
        elif not rn.eh_tipo_visita_tecnica_leite(r.get("tipo_visita")):
            motivo = "TIPO_FORA_WHITELIST_LEITE"
        elif rn.eh_grupo_excluido(r.get("nome_consultor")):
            motivo = "CONSULTOR_EXCLUIDO_BI"
        elif not consultores:
            motivo = "CONSULTOR_NAO_CAMPO"
        elif rn.chave(r.get("nome_consultor")) in rn.GRUPOS_CFT_EXCLUSIVOS:
            motivo = "GRUPO_CFT_EXCLUSIVO"
        elif not rn.eh_cadeia_leite(r.get("projeto")) or not rn.eh_cadeia_leite(projeto):
            motivo = "PROJETO_FORA_LEITE"
        elif unidade == "UNIDADE GENERICA" or rn.chave(r.get("projeto")) == "UNIDADE GENERICA":
            motivo = "UNIDADE_GENERICA"
        else:
            dt_inat = ctx.inativacao_mais_recente.get(cod)
            na_carteira = (cod, mes) in ctx.fazenda_mes
            if dt_inat is not None and r["data_visita"] > dt_inat and not na_carteira:
                motivo = "VISITA_APOS_INATIVACAO"

        auditoria.append({
            "id_atendimento": r["id_atendimento"], "codigo_lr": cod, "nome_consultor": r.get("nome_consultor"),
            "data_visita": r["data_visita"], "tipo_visita": r.get("tipo_visita"), "projeto": r.get("projeto"),
            "status_reconciliacao": "MANTIDA_FATO" if motivo is None else f"EXCLUIDA_{motivo}",
        })
        if motivo:
            continue

        consultor = consultores[0]
        agro = ctx.agroindustria(cod, projeto, mes)
        dt_inat = ctx.inativacao_mais_recente.get(cod)
        # Comparação por data (não por mês): visita anterior à inativação foi feita com a fazenda ativa.
        inativo = dt_inat is not None and r["data_visita"] > dt_inat and (cod, mes) not in ctx.fazenda_mes
        elab = ctx.elabore(cod, mes)
        linhas.append({
            "id_composto": _hash_atendimento(r["id_atendimento"]),
            "id_atendimento": r["id_atendimento"],
            "codigo_lr": cod,
            "nome_consultor": consultor,
            "profissao_consultor": ctx.profissao.get(rn.chave(consultor)),
            "mes_referencia": mes,
            "data_visita": r["data_visita"],
            "tipo_visita": _texto(r.get("tipo_visita")),
            "nome_produtor": _texto(r.get("nome_produtor")) or _texto(faz.get("nome_produtor")) or "NÃO INFORMADO",
            "nome_propriedade": _propriedade_valida(faz.get("nome_propriedade")) or _propriedade_valida(r.get("nome_propriedade")) or "PROPRIEDADE",
            "projeto": projeto or "NÃO INFORMADO",
            "agroindustria": agro,
            "regiao": ctx.regiao_de(cod, agro, projeto, mes),
            "codigo_agroindustria": _texto(faz.get("codigo_agroindustria")),
            "id_farm": _texto(r.get("id_farm")) or _texto(faz.get("codigo_fazenda")),
            "valor_pago_produtor": float(r["valor_pago_produtor"]) if pd.notna(r.get("valor_pago_produtor")) else 0.0,
            "valor_pago_agroindustria": float(r["valor_pago_agroindustria"]) if pd.notna(r.get("valor_pago_agroindustria")) else 0.0,
            "area_pecuaria_ha": _numero(r.get("area_pecuaria_ha")),
            "mdo_dias_homem": _numero(r.get("mdo_dias_homem")),
            "producao_l_dia": _numero(r.get("producao_l_dia")),
            "ccs_mensal": _numero(r.get("ccs_mensal")),
            "cpp_mensal": _numero(r.get("cpp_mensal")),
            "gordura_mensal": _numero(r.get("gordura_mensal")),
            "proteina_mensal": _numero(r.get("proteina_mensal")),
            "vacas_lactacao": _inteiro(r.get("vacas_lactacao")),
            "vacas_secas": _inteiro(r.get("vacas_secas")),
            "bezerras_aleitamento": _inteiro(r.get("bezerras_aleitamento")),
            "bezerros_aleitamento": _inteiro(r.get("bezerros_aleitamento")),
            "novilhas": _inteiro(r.get("novilhas")),
            "reprodutores": _inteiro(r.get("reprodutores")),
            "receptoras": _inteiro(r.get("receptoras")),
            "rebanho_total": _inteiro(r.get("rebanho_total")),
            "status_produtor": "INATIVO" if inativo else "ATIVO",
            "origem_dados": _texto(r.get("origem_dados")),
            **elab,
        })
    linhas_pro, auditoria_pro = construir_visitas_procampo(f, ctx)
    return (pd.DataFrame(linhas + linhas_pro),
            pd.DataFrame(auditoria + auditoria_pro))


def construir_visitas_procampo(f: Fontes, ctx: Contexto) -> tuple[list[dict], list[dict]]:
    """Visitas da planilha do Procampo (pro_raw_visitas) no mesmo layout das visitas do SmartQuestion.

    Sem whitelist de tipo: todos os tipos de visita do Procampo contam. Ficam de fora rascunhos, visitas sem
    fazenda na base e — enquanto procampo.contar_visitas_em_fazenda_lr for false — visitas em fazenda LR######,
    cujas visitas são gerenciadas pelo SmartQuestion. A planilha não traz número de atendimento: o id é gerado.
    """
    if f.pro_visitas.empty:
        return [], []
    cfg = ctx.pro_cfg
    contar_lr = bool(cfg.get("contar_visitas_em_fazenda_lr", False))
    projeto_padrao = cfg.get("projeto_bi", "PROCAMPO")
    v = f.pro_visitas.copy()
    v["data_visita"] = _data(v["data_visita"])
    v["codigo_lr"] = v["codigo_lr"].map(lambda c: rn.limpar_codigo_lr(c) if _texto(c) else "")
    v["mes_referencia"] = v["data_visita"].dt.to_period("M").dt.to_timestamp()
    ids = rn.ids_atendimento_procampo(v["id_composto"])

    linhas, auditoria = [], []
    for r in v.to_dict("records"):
        cod, mes = r["codigo_lr"], r["mes_referencia"]
        id_atend = ids[r["id_composto"]]
        consultores = rn.sanitizar_consultores(r.get("nome_consultor"))
        rascunho = r.get("rascunho")
        if rascunho is not None and not (isinstance(rascunho, float) and math.isnan(rascunho)) and bool(rascunho):
            motivo = "RASCUNHO"
        elif pd.isna(r["data_visita"]):
            motivo = "SEM_DATA_VISITA"
        elif r["data_visita"] > ctx.agora:
            motivo = "DATA_VISITA_FUTURA"
        elif not cod:
            motivo = "SEM_FAZENDA_NA_BASE"
        elif rn.eh_fazenda_smartquestion(cod) and not contar_lr:
            motivo = "FAZENDA_GERENCIADA_PELO_SMARTQUESTION"
        elif rn.eh_grupo_excluido(r.get("nome_consultor")):
            motivo = "CONSULTOR_EXCLUIDO_BI"
        elif not consultores:
            motivo = "CONSULTOR_NAO_CAMPO"
        else:
            motivo = None
        auditoria.append({
            "id_atendimento": id_atend, "codigo_lr": cod, "nome_consultor": r.get("nome_consultor"),
            "data_visita": r["data_visita"], "tipo_visita": r.get("tipo_visita"), "projeto": projeto_padrao,
            "status_reconciliacao": "MANTIDA_FATO" if motivo is None else f"EXCLUIDA_{motivo}",
        })
        if motivo:
            continue

        faz = ctx.fazenda(cod, mes)
        projeto = ctx.projeto(cod, mes, fallback=projeto_padrao)
        agro = ctx.agroindustria(cod, projeto, mes)
        linhas.append({
            "id_composto": hashlib.sha256(f"PROCAMPO_VISITA_{r['id_composto']}".encode("utf-8")).hexdigest(),
            "id_atendimento": id_atend,
            "codigo_lr": cod,
            "nome_consultor": rn.consultor_procampo(consultores[0], cfg.get("aliases_consultor")),   # = consultor da carteira (dim)
            "profissao_consultor": ctx.profissao.get(rn.consultor_procampo(consultores[0], cfg.get("aliases_consultor"))),
            "mes_referencia": mes,
            "data_visita": r["data_visita"],
            "tipo_visita": _texto(r.get("tipo_visita")),
            "nome_produtor": _texto(faz.get("nome_produtor")) or _texto(r.get("nome_produtor")) or "NÃO INFORMADO",
            "nome_propriedade": _propriedade_valida(faz.get("nome_propriedade")) or _propriedade_valida(r.get("nome_propriedade")) or "PROPRIEDADE",
            "projeto": projeto or projeto_padrao,
            "agroindustria": agro,
            "regiao": ctx.regiao_de(cod, agro, projeto, mes),
            "codigo_agroindustria": _texto(faz.get("codigo_agroindustria")) or _texto(r.get("matricula")),
            "id_farm": None,
            "valor_pago_produtor": 0.0,
            "valor_pago_agroindustria": 0.0,
            "area_pecuaria_ha": None,
            "mdo_dias_homem": None,
            "producao_l_dia": _numero(r.get("producao_l_dia")),
            "ccs_mensal": _numero(r.get("ccs_mensal")),
            "cpp_mensal": _numero(r.get("cpp_mensal")),
            "gordura_mensal": _numero(r.get("gordura_mensal")),
            "proteina_mensal": _numero(r.get("proteina_mensal")),
            "vacas_lactacao": _inteiro(r.get("vacas_lactacao")),
            "vacas_secas": None, "bezerras_aleitamento": None, "bezerros_aleitamento": None,
            "novilhas": None, "reprodutores": None, "receptoras": None, "rebanho_total": None,
            "status_produtor": "ATIVO",   # visita no mês mantém a fazenda na carteira do mês
            "origem_dados": "PROCAMPO",
            **ctx.elabore(cod, mes),
        })
    return linhas, auditoria


# ─── tab_fato_carteira_mensal ──────────────────────────────────────────────────

def _melhor_grupo(grupos: list) -> str:
    """Prefere o grupo com co-consultores ('/') ou o mais completo, como a LISTA_GERAL."""
    validos = [g for g in (_texto(x) for x in grupos) if g and rn.chave(g) not in ("NAO ATRIBUIDO",)]
    if not validos:
        return ""
    return sorted(validos, key=lambda g: ("/" in g, len(g)), reverse=True)[0]


def construir_carteira_mensal(f: Fontes, ctx: Contexto, visitas: pd.DataFrame) -> pd.DataFrame:
    grupos_lista_geral: dict[str, list] = {}
    for r in f.raw_fazendas_grupo.to_dict("records"):
        cod = rn.limpar_codigo_lr(r.get("codigo_produtor"))
        grupos_lista_geral.setdefault(cod, []).extend([r.get("consultor"), r.get("grupo_ponto_atendimento")])

    vis = visitas[["codigo_lr", "nome_consultor", "mes_referencia", "data_visita"]].copy() if not visitas.empty else \
        pd.DataFrame(columns=["codigo_lr", "nome_consultor", "mes_referencia", "data_visita"])
    qtd_mes = vis.groupby(["codigo_lr", "mes_referencia"]).size().to_dict()
    qtd_consultor_mes = vis.groupby(["codigo_lr", "mes_referencia", "nome_consultor"]).size().to_dict()
    datas_por_codigo = {cod: sorted(g["data_visita"].tolist()) for cod, g in vis.groupby("codigo_lr")}

    linhas = []
    for r in ctx.dim_leite.to_dict("records"):
        cod, mes = r["codigo_lr"], r["mes_referencia"]
        grupo = _melhor_grupo([r.get("grupo_ponto_atendimento"), *grupos_lista_geral.get(cod, [])])
        grupo_k = rn.chave(grupo)
        projeto = _texto(r.get("projeto")) or rn.mapear_agroindustria(grupo)
        if not cod or rn.eh_codigo_conta_consultor(cod):
            continue
        if ("SUPERVISAO" in grupo_k or "AGRICULTURA" in grupo_k) and not cod.startswith("LR"):
            continue
        if not rn.eh_cadeia_leite(projeto):
            continue
        if rn.eh_grupo_excluido(grupo):
            continue  # carteira só de consultor removido do BI (CONSULTORES_EXCLUIDOS_BI)
        consultores = rn.sanitizar_consultores(grupo) or [rn.NAO_ATRIBUIDO]
        sem_lancamento = rn.eh_grupo_sem_lancamento_elabore(grupo)   # regra 13 do AGENTS.md

        fim_mes = mes + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59, seconds=59)
        data_corte = min(ctx.agora, fim_mes)
        datas = datas_por_codigo.get(cod, [])
        anteriores = [d for d in datas if d <= data_corte]
        ultima = anteriores[-1] if anteriores else None
        mes_anterior = mes - pd.DateOffset(months=1)
        no_mes_anterior = [d for d in datas if d.to_period("M") == mes_anterior.to_period("M")]
        assoc = ctx.data_associacao.get(cod)
        if ultima is not None:
            dias = max(0, (data_corte - ultima).days)
        elif assoc is not None:
            dias = max(0, (data_corte - assoc).days)
        else:
            dias = max(0, (data_corte - mes).days)
        dias_assoc = max(0, (data_corte - assoc).days) if assoc is not None else None
        dt_inat = ctx.inativacao_mais_recente.get(cod)
        pendente = dt_inat is not None and dt_inat <= data_corte
        qtd = int(qtd_mes.get((cod, mes), 0))
        if qtd > 0:
            status, classe = "Visitado", "badge-positive"
        else:
            status, classe = rn.classificar_status_sem_visita(pendente, dias, ultima is not None, dias_assoc)

        agro = ctx.agroindustria(cod, projeto, mes)
        regiao = ctx.regiao_de(cod, agro, projeto, mes)
        inicio_vinculo = assoc.to_period("M").to_timestamp() if assoc is not None else None
        carencia_fim = inicio_vinculo + pd.DateOffset(months=2) if inicio_vinculo is not None else None
        elab = ctx.elabore(cod, mes)
        consist = ctx.consistencia(cod, mes)

        for consultor in consultores:
            linhas.append({
                "id_composto": f"{cod}|{mes:%Y-%m-%d}|{consultor}",
                "mes_referencia": mes,
                "codigo_lr": cod,
                "consultor": consultor,
                "consultores_grupo": " / ".join(consultores),
                "profissao_consultor": ctx.profissao.get(rn.chave(consultor)),
                "nome_produtor": _texto(r.get("nome_produtor"), "PRODUTOR SEM NOME"),
                "nome_propriedade": _propriedade_valida(r.get("nome_propriedade")) or "PROPRIEDADE",
                "estado": _texto(r.get("estado")),
                "cidade": _texto(r.get("cidade")),
                "projeto": projeto,
                "agroindustria": agro,
                "regiao": regiao,
                "data_associacao": assoc,
                "qtd_visitas_mes": qtd,
                "visitas_consultor_mes": int(qtd_consultor_mes.get((cod, mes, consultor), 0)),
                "visitado_mes": qtd > 0,
                "data_ultima_visita": ultima,
                "data_visita_mes_anterior": no_mes_anterior[-1] if no_mes_anterior else None,
                "dias_sem_visita": int(dias),
                "inativacao_pendente": bool(pendente),
                "status_visita": status,
                "status_visita_classe": classe,
                **consist,
                "excecao": False,
                "sem_lancamento_elabore": sem_lancamento,
                "em_carencia": bool(carencia_fim is not None and carencia_fim > ctx.agora),
                **elab,
            })
    return pd.DataFrame(linhas)


# ─── tab_fato_consistencia ─────────────────────────────────────────────────────

def construir_fato_consistencia(f: Fontes, ctx: Contexto, carteira: pd.DataFrame, data_inicial: str) -> pd.DataFrame:
    na_carteira = set(zip(carteira["codigo_lr"], carteira["mes_referencia"])) if not carteira.empty else set()
    principal = carteira.drop_duplicates(subset=["codigo_lr", "mes_referencia"]).set_index(["codigo_lr", "mes_referencia"]) \
        if not carteira.empty else pd.DataFrame()
    cm = ctx.consistencia_mensal
    cm = cm[cm["mes_referencia"] >= pd.Timestamp(data_inicial)]
    linhas = []
    for r in cm.to_dict("records"):
        cod, mes = r["codigo_lr"], r["mes_referencia"]
        if not cod or "TESTE" in cod or "LABOR" in cod or pd.isna(mes):
            continue
        ativo = (cod, mes) in na_carteira
        if ativo:
            p = principal.loc[(cod, mes)]
            projeto, agro, regiao, consultor = p["projeto"], p["agroindustria"], p["regiao"], p["consultor"]
            nome = p["nome_produtor"]
            sem_lancamento = bool(p["sem_lancamento_elabore"])
        else:
            faz = ctx.fazenda(cod)
            inat = ctx.inativacao_meta.get(cod, {})
            vinc = ctx.vinculo_recente.get(cod, {})
            projeto = ctx.projeto(cod, fallback=inat.get("projeto"))
            if not rn.eh_cadeia_leite(projeto):
                continue
            grupo = faz.get("grupo_ponto_atendimento") or vinc.get("consultor_grupo_atendimento") or inat.get("grupo_ponto_atendimento")
            if rn.eh_grupo_excluido(grupo):
                continue
            consultor =(rn.sanitizar_consultores(grupo) or [rn.NAO_ATRIBUIDO])[0]
            sem_lancamento = rn.eh_grupo_sem_lancamento_elabore(grupo)
            agro = ctx.agroindustria(cod, projeto)
            regiao = ctx.regiao_de(cod, agro, projeto)
            nome = _texto(faz.get("nome_produtor")) or _texto(vinc.get("nome_produtor")) or _texto(r.get("nome_produtor")) or cod
        assoc = ctx.data_associacao.get(cod)
        inicio_vinculo = assoc.to_period("M").to_timestamp() if assoc is not None else None
        linhas.append({
            "id_composto": f"{cod}|{mes:%Y-%m-%d}",
            "codigo_lr": cod,
            "nome_consultor": consultor,
            "profissao_consultor": ctx.profissao.get(rn.chave(consultor)),
            "nome_produtor": nome,
            "projeto": projeto or "NÃO INFORMADO",
            "agroindustria": agro,
            "regiao": regiao,
            "mes_referencia": mes,
            "data_carencia_fim": inicio_vinculo + pd.DateOffset(months=2) if inicio_vinculo is not None else None,
            "status_code": "active_approved",
            **ctx.consistencia(cod, mes),
            "excecao": 0,
            "sem_lancamento_elabore": sem_lancamento,
            "na_carteira": ativo,
            **ctx.elabore(cod, mes),
        })
    df = pd.DataFrame(linhas)
    return df.rename(columns={"detalhamento_mensal": "detalhamento_inconsistencia"})


# ─── tab_fato_movimentacao ─────────────────────────────────────────────────────

def construir_fato_movimentacao(f: Fontes, ctx: Contexto) -> pd.DataFrame:
    linhas = []
    for r in f.fato_movimentacao.to_dict("records"):
        cod = rn.limpar_codigo_lr(r.get("codigo_lr"))
        consultores = rn.sanitizar_consultores(r.get("nome_consultor"))
        consultor = consultores[0] if consultores else rn.NAO_ATRIBUIDO
        inat = ctx.inativacao_meta.get(cod, {})
        projeto = ctx.projeto(cod, fallback=inat.get("projeto"))
        if not rn.eh_cadeia_leite(projeto) or rn.eh_grupo_excluido(r.get("nome_consultor")):
            continue
        saida = "sa" in str(r.get("movimentacao") or "").lower()
        tipo = "SAÍDA" if saida else "ENTRADA"
        agro = ctx.agroindustria(cod, projeto)
        faz = ctx.fazenda(cod)
        nome = (_texto(r.get("nome_produtor")) or _texto(faz.get("nome_produtor"))
                or _texto(ctx.vinculo_qualquer.get(cod, {}).get("nome_produtor")) or _texto(inat.get("nome_produtor"))
                or ("CONTA DE SUPERVISÃO" if rn.eh_codigo_conta_consultor(cod) else cod or "PRODUTOR"))
        linhas.append({
            **{k: r.get(k) for k in ("id_composto", "codigo_lr", "numero_atendimento", "data_movimentacao", "data_solicitacao")},
            "nome_produtor": nome,
            "consultor": consultor,
            "tipo": tipo,
            "motivo": _texto(r.get("motivo_inativacao")) or _texto(r.get("outro_motivo")) or ("Desligamento" if saida else "Novo Cadastro"),
            "projeto": projeto or "NÃO INFORMADO",
            "agroindustria": agro,
            "regiao": ctx.regiao_de(cod, agro, projeto),
        })
    return pd.DataFrame(linhas + construir_movimentacao_procampo(f, ctx))


def construir_movimentacao_procampo(f: Fontes, ctx: Contexto) -> list[dict]:
    """Entradas e saídas do Procampo (pro_raw_movimentacao), só de fazendas PRO#### e provisórias.

    ENTRADA = "Inclusão do produtor na Consultoria", na DATA.INÍCIO. SAÍDA = "SAÍDA"/"Inativação", na
    DATA.CONCLUSÃO. "Legado" e trocas de consultor/titularidade não são movimentação de produtor.
    Eventos anteriores ao primeiro mês da carteira ficam de fora (a carteira do BI começa em 2026).
    """
    if f.pro_movimentacao.empty:
        return []
    projeto_padrao = ctx.pro_cfg.get("projeto_bi", "PROCAMPO")
    piso = ctx.dim_leite["mes_referencia"].min() if not ctx.dim_leite.empty else pd.NaT
    m = f.pro_movimentacao.copy()
    m["data_inicio"] = _data(m["data_inicio"])
    m["data_conclusao"] = _data(m["data_conclusao"])
    # Um mesmo evento pode estar nas duas abas da base mestra (ex.: inclusão recente em ATIVOS e em ROTATIVIDADE).
    # Fica um por (fazenda, tipo, data): ENTRADA vale a linha de ATIVOS; SAÍDA, a de ROTATIVIDADE.
    unicas: dict[tuple, dict] = {}
    sem_data, antigos, repetidos = 0, 0, 0
    for r in m.to_dict("records"):
        cod = rn.limpar_codigo_lr(r.get("codigo_lr")) if _texto(r.get("codigo_lr")) else ""
        tipo = rn.classificar_movimentacao_procampo(r.get("status_movimentacao"))
        if not cod or rn.eh_fazenda_smartquestion(cod) or tipo is None:
            continue
        consultores = rn.sanitizar_consultores(r.get("consultor"))
        if rn.eh_grupo_excluido(r.get("consultor")):
            continue
        data = r["data_conclusao"] if tipo == "SAIDA" else r["data_inicio"]
        if pd.isna(data):
            sem_data += 1
            continue
        if pd.notna(piso) and data < piso:
            antigos += 1
            continue
        projeto = ctx.projeto(cod, fallback=projeto_padrao)
        agro = ctx.agroindustria(cod, projeto)
        faz = ctx.fazenda(cod)
        saida = tipo == "SAIDA"
        chave_evento = (cod, tipo, data.strftime("%Y-%m-%d"))
        aba_preferida = "ROTATIVIDADE" if saida else "ATIVOS"
        if chave_evento in unicas:
            repetidos += 1
            if rn.chave(r.get("aba_origem")) != aba_preferida:
                continue
        linha = {
            "id_composto": hashlib.sha256(f"PROCAMPO_MOV_{cod}|{tipo}|{chave_evento[2]}".encode("utf-8")).hexdigest(),
            "codigo_lr": cod,
            "numero_atendimento": None,
            "data_movimentacao": data,
            "data_solicitacao": None,
            "nome_produtor": _texto(faz.get("nome_produtor")) or cod,
            "consultor": rn.consultor_procampo(consultores[0], ctx.pro_cfg.get("aliases_consultor")) if consultores else rn.NAO_ATRIBUIDO,
            "tipo": "SAÍDA" if saida else "ENTRADA",
            "motivo": _texto(r.get("motivo_saida")) or ("Desligamento" if saida else "Novo Cadastro"),
            "projeto": projeto or projeto_padrao,
            "agroindustria": agro,
            "regiao": ctx.regiao_de(cod, agro, projeto),
        }
        unicas[chave_evento] = linha
    if repetidos:
        print(f"   ℹ️ Movimentação Procampo: {repetidos} eventos repetidos entre ATIVOS e ROTATIVIDADE foram unificados.")
    if sem_data or antigos:
        print(f"   ℹ️ Movimentação Procampo: {sem_data} eventos sem data e {antigos} anteriores a {piso:%Y-%m} ficaram de fora.")
    return list(unicas.values())


# ─── tab_fato_producao_mensal ──────────────────────────────────────────────────

COLS_PRODUCAO = ["id_composto", "codigo_lr", "mes_referencia", "nome_produtor", "nome_propriedade", "nome_consultor",
                 "projeto", "agroindustria", "regiao", "status_produtor", "origem_dados", "data_visita", "n_visitas_mes",
                 "producao_l_dia", "producao_l_dia_bruto", "vacas_lactacao", "produtividade_l_vaca_dia",
                 "ccs", "cpp", "gordura", "proteina", "producao_suspeita", "producao_imputada",
                 "ccs_acima_limite", "cpp_acima_limite", "qualidade_descartada"]

_CAMPOS_MEDICAO = ["producao_l_dia", "vacas_lactacao", "ccs_mensal", "cpp_mensal", "gordura_mensal", "proteina_mensal"]


def _acima(valor, limite: float) -> bool:
    n = _numero(valor)
    return n is not None and n > limite


def _qualidade_valida(valor, faixa) -> tuple[float | None, bool]:
    """(valor utilizável, foi descartado). Zero/vazio = não medido (não conta como descarte); positivo fora da
    faixa plausível = erro de digitação: vira None."""
    n = _numero(valor)
    if n is None or n <= 0:
        return None, False
    if not (faixa[0] <= n <= faixa[1]):
        return None, True
    return n, False


def construir_producao_mensal(visitas: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Volume, produtividade e qualidade do leite por fazenda × mês, a partir da tab_fato_visitas já resolvida.

    Vale a última visita do mês com medição (as listas gerais de visita não trazem produção; só Procampo, Regenera,
    Alvoar, Semear, CCPR e LPA). O registro é suspeito quando a produtividade sai da faixa plausível ou o volume salta
    `fator_salto` vezes contra a mediana dos outros meses do produtor (regras_negocio.producao_suspeita). Com
    `imputar_suspeitos`, o volume suspeito vira a média dos meses válidos e o bruto fica em producao_l_dia_bruto."""
    if visitas.empty:
        return pd.DataFrame(columns=COLS_PRODUCAO)
    lim_min = float(cfg.get("produtividade_min", 3.0))
    lim_max = float(cfg.get("produtividade_max", 55.0))
    fator = float(cfg.get("fator_salto", 3.0))
    imputar = bool(cfg.get("imputar_suspeitos", True))
    lim_ccs = float(cfg.get("limite_ccs", 500))
    lim_cpp = float(cfg.get("limite_cpp", 300))
    faixas = {"ccs": cfg.get("faixa_ccs", [1, 5000]), "cpp": cfg.get("faixa_cpp", [1, 5000]),
              "gordura": cfg.get("faixa_gordura", [1, 10]), "proteina": cfg.get("faixa_proteina", [1.5, 7])}
    sem_ccs = {rn.chave(a) for a in cfg.get("agroindustrias_sem_ccs_confiavel", [])}

    v = visitas.copy()
    for c in _CAMPOS_MEDICAO:
        v[c] = pd.to_numeric(v[c], errors="coerce")
    v = v[v[_CAMPOS_MEDICAO].notna().any(axis=1)].copy()
    if v.empty:
        return pd.DataFrame(columns=COLS_PRODUCAO)
    v["_tem_volume"] = v["producao_l_dia"].notna() & (v["producao_l_dia"] > 0)
    v["data_visita"] = _data(v["data_visita"])
    v = v.sort_values(["codigo_lr", "mes_referencia", "_tem_volume", "data_visita"])
    n_visitas = v.groupby(["codigo_lr", "mes_referencia"]).size().rename("n_visitas_mes")
    ultimas = v.groupby(["codigo_lr", "mes_referencia"], as_index=False).tail(1).copy()
    ultimas = ultimas.merge(n_visitas.reset_index(), on=["codigo_lr", "mes_referencia"], how="left")

    ultimas["producao_l_dia_bruto"] = ultimas["producao_l_dia"].where(ultimas["_tem_volume"])
    linhas = []
    for cod, g in ultimas.sort_values("mes_referencia").groupby("codigo_lr"):
        registros = g.to_dict("records")
        brutos = [r["producao_l_dia_bruto"] for r in registros]
        suspeitos = []
        for i, r in enumerate(registros):
            outros = [b for j, b in enumerate(brutos) if j != i and pd.notna(b)]
            mediana = float(pd.Series(outros).median()) if len(outros) >= 2 else None
            suspeitos.append(rn.producao_suspeita(r["producao_l_dia_bruto"], r["vacas_lactacao"], mediana,
                                                  lim_min, lim_max, fator))
        validos = [b for b, s in zip(brutos, suspeitos) if pd.notna(b) and not s]
        media_valida = float(sum(validos) / len(validos)) if validos else None
        for r, suspeito in zip(registros, suspeitos):
            bruto = r["producao_l_dia_bruto"]
            volume = None if pd.isna(bruto) else float(bruto)
            imputado = False
            if suspeito and imputar:
                volume, imputado = media_valida, media_valida is not None
            elif suspeito:
                volume = float(bruto)
            prod = rn.produtividade_l_vaca_dia(volume, r["vacas_lactacao"])
            if prod is not None and not (lim_min <= prod <= lim_max):
                prod = None   # vacas em lactação inconsistentes com o volume: melhor sem produtividade que errada
            qual, descartada = {}, False
            ccs_fora = rn.chave(r.get("agroindustria")) in sem_ccs   # escala própria: não é "erro de digitação"
            for campo, origem in (("ccs", "ccs_mensal"), ("cpp", "cpp_mensal"), ("gordura", "gordura_mensal"),
                                  ("proteina", "proteina_mensal")):
                if campo == "ccs" and ccs_fora:
                    qual[campo] = None
                    continue
                qual[campo], d = _qualidade_valida(r.get(origem), faixas[campo])
                descartada = descartada or d
            linhas.append({
                "id_composto": f"{cod}|{r['mes_referencia']:%Y-%m-%d}",
                "codigo_lr": cod, "mes_referencia": r["mes_referencia"],
                "nome_produtor": r.get("nome_produtor"), "nome_propriedade": r.get("nome_propriedade"),
                "nome_consultor": r.get("nome_consultor"), "projeto": r.get("projeto"),
                "agroindustria": r.get("agroindustria"), "regiao": r.get("regiao"),
                "status_produtor": r.get("status_produtor"), "origem_dados": r.get("origem_dados"),
                "data_visita": r["data_visita"], "n_visitas_mes": int(r["n_visitas_mes"]),
                "producao_l_dia": None if volume is None else round(volume, 2),
                "producao_l_dia_bruto": None if pd.isna(bruto) else float(bruto),
                "vacas_lactacao": _inteiro(r.get("vacas_lactacao")),
                "produtividade_l_vaca_dia": None if prod is None else round(prod, 2),
                "ccs": qual["ccs"], "cpp": qual["cpp"], "gordura": qual["gordura"], "proteina": qual["proteina"],
                "producao_suspeita": bool(suspeito), "producao_imputada": bool(imputado),
                "ccs_acima_limite": _acima(qual["ccs"], lim_ccs),
                "cpp_acima_limite": _acima(qual["cpp"], lim_cpp),
                "qualidade_descartada": bool(descartada),
            })
    return pd.DataFrame(linhas, columns=COLS_PRODUCAO)


# ─── tab_fato_safra_forrageira (slide 7) ───────────────────────────────────────

COLS_SAFRA = ["id_composto", "bloco", "codigo_lr", "nome_produtor", "nome_propriedade", "consultor", "consultores_grupo",
              "projeto", "agroindustria", "regiao", "safra", "cultura", "ciclo", "produto", "sistema", "fonte",
              "n_plantios", "area_plantada_ha", "area_com_cobertura_ha", "area_cobertura_declarada_ha",
              "area_sucessao_ha", "n_producoes", "producoes_inconsistentes", "area_colhida_ha", "toneladas",
              "custo_total", "custo_pre_plantio", "custo_plantio", "custo_tratos", "custo_colheita",
              "custo_etapa_nao_informada", "custo_total_defl", "custo_pre_plantio_defl", "custo_plantio_defl",
              "custo_tratos_defl", "custo_colheita_defl", "custo_etapa_nao_informada_defl"]

# Ciclo da origem (já em maiúsculo sem acento) -> código estável usado pela API/tela
CICLOS_SAFRA = {"1A SAFRA": "1_SAFRA", "2A SAFRA": "2_SAFRA", "3A SAFRA": "3_SAFRA",
                "PLANTAS DE COBERTURA": "COBERTURA", "FORMACAO": "FORMACAO", "PRODUCAO": "PRODUCAO"}
SISTEMAS_SAFRA = {"PLANTIO DIRETO", "CULTIVO MINIMO", "CONVENCIONAL"}
SISTEMA_NAO_INFORMADO = "NAO INFORMADO"
ALIAS_CULTURA_SAFRA = {"BRACHIARIA": "BRAQUIARIA"}


def _cultura_safra(v) -> str | None:
    c = rn.chave(_texto(v)) if _texto(v) else None
    return ALIAS_CULTURA_SAFRA.get(c, c) if c else None


def _sistema_safra(v) -> str:
    s = rn.chave(_texto(v)) if _texto(v) else ""
    return s if s in SISTEMAS_SAFRA else SISTEMA_NAO_INFORMADO


def _ciclo_safra(v) -> str:
    return CICLOS_SAFRA.get(rn.chave(_texto(v)) if _texto(v) else "", "NAO_INFORMADO")


def _verdade(serie: pd.Series) -> pd.Series:
    return serie.map(lambda v: v is True or str(v).strip().lower() == "true")


def construir_safra_forrageira(f: Fontes, carteira: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Indicadores de safra por fazenda × safra × cultura × ciclo × sistema, em dois blocos:

      AREA   (elabore_raw_sistema_cobertura): área plantada por sistema de plantio e quanto dela recebeu cobertura.
             "Com cobertura" segue safra_forrageira.regra_cobertura: 'cobertura' = só planta de cobertura declarada;
             'sucessao' = também cultivo de 2ª/3ª safra no mesmo talhão até 365 dias depois.
      CUSTO  (elabore_raw_custo_forrageira): por produto colhido, área colhida, toneladas e custo NOMINAL por etapa,
             só das produções consistentes (com quantidade, sem ciclo divergente, sem alerta de custo/ha e com
             categoria válida); as demais ficam contadas em producoes_inconsistentes.

    Agroindústria, região e consultor vêm do registro mais recente da fazenda na carteira (a mesma resolução dos
    demais slides e dos filtros). Fazenda fora da carteira de leite não entra (é contada no resumo)."""
    resumo = {"sem_carteira": 0, "codigos_sem_carteira": [], "linhas_sem_codigo": 0}
    if f.safra_custo.empty and f.safra_sistema.empty:
        return pd.DataFrame(columns=COLS_SAFRA), resumo
    cfg = f.cfg_safra or {}
    regra = str(cfg.get("regra_cobertura", "sucessao")).lower()
    categorias = {rn.chave(c) for c in cfg.get("categorias_validas", ["VOLUMOSO", "CONCENTRADO"])}

    # Cadastro da fazenda: linha mais recente da carteira (1 consultor por fazenda; o grupo inteiro fica no texto)
    cadastro: dict[str, dict] = {}
    if carteira is not None and not carteira.empty:
        base = carteira.sort_values(["codigo_lr", "mes_referencia", "consultor"], ascending=[True, False, True])
        cadastro = {r["codigo_lr"]: r for r in base.drop_duplicates("codigo_lr").to_dict("records")}

    def preparar(df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        d["codigo_lr"] = d["codigo_lr"].map(rn.limpar_codigo_lr)
        sem_codigo = d["codigo_lr"].isna() | (d["codigo_lr"] == "")
        resumo["linhas_sem_codigo"] += int(sem_codigo.sum())
        d = d[~sem_codigo].copy()
        d["cultura"] = d["cultura"].map(_cultura_safra)
        d["ciclo"] = d["ciclo"].map(_ciclo_safra)
        d["sistema"] = d["sistema_plantio"].map(_sistema_safra)
        d["safra"] = d["safra"].map(_texto)
        return d[d["safra"].notna() & d["cultura"].notna()]

    linhas = []

    if not f.safra_sistema.empty:
        s = preparar(f.safra_sistema)
        s["fonte"] = s["fonte"].map(lambda v: rn.chave(_texto(v, "ELABORE")))
        s["area_plantada_ha"] = pd.to_numeric(s["area_plantada_ha"], errors="coerce").fillna(0.0)
        cob, suc = _verdade(s["cobertura_apos"]), _verdade(s["sucessao_apos"])
        s["_cob"] = s["area_plantada_ha"].where(cob, 0.0)
        s["_suc"] = s["area_plantada_ha"].where(suc, 0.0)
        s["_com"] = s["area_plantada_ha"].where(suc if regra == "sucessao" else cob, 0.0)
        g = s.groupby(["codigo_lr", "safra", "cultura", "ciclo", "sistema", "fonte"], dropna=False).agg(
            n_plantios=("id_plantio", "size"), area_plantada_ha=("area_plantada_ha", "sum"),
            area_com_cobertura_ha=("_com", "sum"), area_cobertura_declarada_ha=("_cob", "sum"),
            area_sucessao_ha=("_suc", "sum")).reset_index()
        g["bloco"], g["produto"] = "AREA", None
        linhas.append(g)

    if not f.safra_custo.empty:
        c = preparar(f.safra_custo)
        c["produto"] = c["produto_colhido"].map(lambda v: rn.chave(_texto(v)) if _texto(v) else None)
        num = lambda col: pd.to_numeric(c[col], errors="coerce").fillna(0.0)
        consistente = (
            (num("quantidade_kg") > 0) & (num("area_colhida_ha") > 0)
            & ~_verdade(c["sem_quantidade_produzida"]) & ~_verdade(c["ciclo_divergente"])
            & ~_verdade(c["alerta_custo_ha"])
            & c["categoria"].map(lambda v: rn.chave(_texto(v, "")) in categorias)
        )
        medidas = {
            "area_colhida_ha": num("area_colhida_ha"), "toneladas": num("quantidade_kg") / 1000.0,
            "custo_total": num("custo_total_nom"), "custo_pre_plantio": num("custo_pre_plantio_nom"),
            "custo_plantio": num("custo_plantio_nom"), "custo_tratos": num("custo_tratos_nom"),
            "custo_colheita": num("custo_colheita_planta_inteira_nom") + num("custo_colheita_grao_nom"),
            "custo_etapa_nao_informada": num("custo_etapa_nao_informada_nom"),
            # Mesmos valores deflacionados pelo IGP-DI na origem (por data de compra): base de comparação entre safras
            "custo_total_defl": num("custo_total_defl"), "custo_pre_plantio_defl": num("custo_pre_plantio_defl"),
            "custo_plantio_defl": num("custo_plantio_defl"), "custo_tratos_defl": num("custo_tratos_defl"),
            "custo_colheita_defl": num("custo_colheita_planta_inteira_defl") + num("custo_colheita_grao_defl"),
            "custo_etapa_nao_informada_defl": num("custo_etapa_nao_informada_defl"),
        }
        for col, valores in medidas.items():
            c[col] = valores.where(consistente, 0.0)
        c["_ok"] = consistente.astype(int)
        c["_nok"] = (~consistente).astype(int)
        c["fonte"] = "ELABORE"
        g = c.groupby(["codigo_lr", "safra", "cultura", "ciclo", "produto", "sistema", "fonte"], dropna=False).agg(
            n_producoes=("_ok", "sum"), producoes_inconsistentes=("_nok", "sum"),
            **{col: (col, "sum") for col in medidas}).reset_index()
        g["bloco"] = "CUSTO"
        linhas.append(g)

    fato = pd.concat(linhas, ignore_index=True, sort=False)
    no_cadastro = fato["codigo_lr"].isin(cadastro.keys())
    resumo["sem_carteira"] = int(fato.loc[~no_cadastro, "codigo_lr"].nunique())
    resumo["codigos_sem_carteira"] = sorted(fato.loc[~no_cadastro, "codigo_lr"].unique())
    fato = fato[no_cadastro].copy()
    for campo in ("nome_produtor", "nome_propriedade", "consultor", "consultores_grupo", "projeto", "agroindustria", "regiao"):
        fato[campo] = fato["codigo_lr"].map(lambda cod: cadastro[cod].get(campo))
    fato["id_composto"] = fato.apply(
        lambda r: "|".join(str(x) for x in (r["bloco"], r["codigo_lr"], r["safra"], r["cultura"], r["ciclo"],
                                             r["produto"] or "-", r["sistema"], r["fonte"])), axis=1)
    if fato["id_composto"].duplicated().any():
        raise ValueError("tab_fato_safra_forrageira com id_composto repetido.")
    for col in COLS_SAFRA:
        if col not in fato.columns:
            fato[col] = None
    for col in ("n_plantios", "n_producoes", "producoes_inconsistentes"):
        fato[col] = pd.to_numeric(fato[col], errors="coerce").astype("Int64")
    for col in ("area_plantada_ha", "area_com_cobertura_ha", "area_cobertura_declarada_ha", "area_sucessao_ha",
                "area_colhida_ha", "toneladas", "custo_total", "custo_pre_plantio", "custo_plantio", "custo_tratos",
                "custo_colheita", "custo_etapa_nao_informada", "custo_total_defl", "custo_pre_plantio_defl",
                "custo_plantio_defl", "custo_tratos_defl", "custo_colheita_defl", "custo_etapa_nao_informada_defl"):
        fato[col] = pd.to_numeric(fato[col], errors="coerce").round(3)
    return fato[COLS_SAFRA].reset_index(drop=True), resumo


# ─── Coordenadas das fazendas (colunas de tab_dim_fazendas_ativas) ─────────────

COLS_LOCALIZACAO = ["codigo_lr", "latitude", "longitude", "origem_coordenada", "coordenada_corrigida", "cidade", "estado"]
COLS_COORD_DIM = ["latitude", "longitude", "origem_coordenada", "coordenada_corrigida"]
# Fontes que chegam prontas à dim (reconciliar_movimentacao_e_ativos.py e publicar_dim_procampo.py) e mandam sobre o Elabore
ORIGENS_PRIMARIAS = {"SMARTQUESTION_VISITAS", "PROCAMPO"}
LIMITE_DIVERGENCIA_ELABORE_KM = 5.0


def _distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def construir_localizacao(f: Fontes, carteira: pd.DataFrame) -> pd.DataFrame:
    """Coordenada de cada fazenda que aparece na carteira.

    Ordem das fontes: (1) primária, já gravada na dim — coordenada do produtor nas planilhas de visita do
    SmartQuestion (origem SMARTQUESTION_VISITAS) ou base do Procampo (PROCAMPO); (2) base do Procampo lida da
    pro_raw_fazendas; (3) Elabore (vw_dim_property). A primária vence porque, quando divergem, a do Elabore é a que
    costuma cair no município errado. Toda coordenada passa por regras_negocio.sanear_coordenada. Sem coordenada
    válida, a fazenda fica na mediana das outras fazendas da mesma cidade/UF (origem 'MUNICIPIO', aproximada) e, se a
    cidade também não tiver nenhuma, sem coordenada (origem 'SEM_COORDENADA').
    As colunas `observacao` e `divergencia_elabore_km` são só do relatório de conferência; não são publicadas."""
    colunas = COLS_LOCALIZACAO + ["agroindustria", "observacao", "divergencia_elabore_km"]
    if carteira.empty:
        return pd.DataFrame(columns=colunas)

    atual = carteira.sort_values("mes_referencia").drop_duplicates("codigo_lr", keep="last")
    props = f.elabore_propriedades.copy()
    props["codigo_lr"] = props["codigo_lr"].map(rn.limpar_codigo_lr)
    props["_aprovada"] = props["property_status"] == "active_approved"
    props = props.sort_values("_aprovada").drop_duplicates("codigo_lr", keep="last")   # aprovada tem prioridade
    elabore = {r["codigo_lr"]: r for r in props.to_dict("records")}
    procampo = {}
    if not f.pro_fazendas.empty and "latitude" in f.pro_fazendas.columns:
        pf = f.pro_fazendas.copy()
        pf["codigo_lr"] = pf["codigo_lr"].map(rn.limpar_codigo_lr)
        procampo = {r["codigo_lr"]: r for r in pf.to_dict("records") if not rn.eh_fazenda_smartquestion(r["codigo_lr"])}
    primaria = {}
    dim = f.dim_fazendas
    if not dim.empty and "latitude" in dim.columns and "origem_coordenada" in dim.columns:
        d = dim[dim["latitude"].notna() & dim["longitude"].notna() & dim["origem_coordenada"].isin(ORIGENS_PRIMARIAS)].copy()
        d["codigo_lr"] = d["codigo_produtor"].map(rn.limpar_codigo_lr)
        for r in d.sort_values("mes_referencia").to_dict("records"):   # o mês mais recente vence
            primaria[r["codigo_lr"]] = r

    linhas = []
    for r in atual.to_dict("records"):
        cod = r["codigo_lr"]
        cidade, estado = _texto(r.get("cidade")), _texto(r.get("estado"))
        cand = []
        if cod in primaria:
            p = primaria[cod]
            cand.append((p["origem_coordenada"], p.get("latitude"), p.get("longitude"), rn.sigla_uf(p.get("estado")) or rn.sigla_uf(estado)))
        if cod in procampo:
            p = procampo[cod]
            cand.append(("PROCAMPO", p.get("latitude"), p.get("longitude"), rn.sigla_uf(p.get("uf"))))
            cidade, estado = cidade or _texto(p.get("cidade")), estado or _texto(p.get("uf"))
        e = elabore.get(cod)
        if e is not None:
            cand.append(("ELABORE", e.get("latitude"), e.get("longitude"), rn.sigla_uf(e.get("uf"))))
            cidade, estado = cidade or _texto(e.get("city")), estado or _texto(e.get("uf"))
        uf_cadastro = rn.sigla_uf(estado)
        achado, tinha_bruta, elabore_valida = None, False, None
        for origem, lat, lon, uf in cand:
            tinha_bruta = tinha_bruta or (pd.notna(lat) and pd.notna(lon) and not (abs(lat) < 0.01 and abs(lon) < 0.01))
            s = rn.sanear_coordenada(lat, lon, uf or uf_cadastro)
            if origem == "ELABORE":
                elabore_valida = s
            if s and achado is None:
                achado = (origem, *s)
        linha = {"codigo_lr": cod, "latitude": None, "longitude": None, "origem_coordenada": "SEM_COORDENADA",
                 "coordenada_corrigida": False, "cidade": cidade, "estado": estado,
                 "agroindustria": r.get("agroindustria"), "observacao": None, "divergencia_elabore_km": None}
        if achado:
            linha.update(origem_coordenada=achado[0], latitude=achado[1], longitude=achado[2], coordenada_corrigida=achado[3])
            if achado[3]:
                linha["observacao"] = "Coordenada corrigida (latitude/longitude trocadas ou sinal perdido)"
            if achado[0] != "ELABORE" and elabore_valida:
                km = _distancia_km(achado[1], achado[2], elabore_valida[0], elabore_valida[1])
                if km > LIMITE_DIVERGENCIA_ELABORE_KM:
                    linha["divergencia_elabore_km"] = round(km, 1)
                    linha["observacao"] = (f"Elabore diverge {km:,.0f} km da coordenada de {achado[0]} (usada a de {achado[0]}); "
                                           "corrigir o cadastro no Elabore").replace(",", ".")
        else:
            linha["observacao"] = ("Coordenada do cadastro inválida ou fora da UF" if tinha_bruta
                                   else "Sem coordenada no cadastro")
        linhas.append(linha)
    df = pd.DataFrame(linhas, columns=colunas)

    # Fallback: mediana das fazendas com coordenada válida da mesma cidade/UF (posição aproximada)
    chave_cidade = df["cidade"].map(lambda c: rn.chave(c) if _texto(c) else "") + "|" + df["estado"].map(lambda e: rn.chave(e) if _texto(e) else "")
    validas = df["latitude"].notna() & (df["origem_coordenada"] != "MUNICIPIO")
    centro = df[validas].groupby(chave_cidade[validas])[["latitude", "longitude"]].median()
    for i in df.index[~df["latitude"].notna()]:
        k = chave_cidade[i]
        if k.startswith("|") or k.endswith("|") or k not in centro.index:
            continue
        df.loc[i, ["latitude", "longitude"]] = centro.loc[k, ["latitude", "longitude"]].round(6).values
        df.loc[i, "origem_coordenada"] = "MUNICIPIO"
        df.loc[i, "observacao"] = (df.loc[i, "observacao"] or "") + " → posição aproximada pelo município"
    return df


def _verdadeiro(valor) -> bool:
    return valor is not None and not pd.isna(valor) and bool(valor)


def publicar_coordenadas_dim(supabase, f: Fontes, localizacao: pd.DataFrame) -> int:
    """Grava a coordenada resolvida nas linhas de LEITE de tab_dim_fazendas_ativas (todos os meses de cada fazenda).

    Reenvia a linha inteira (upsert por id) só quando a coordenada mudou: um upsert parcial falharia nas colunas NOT NULL.
    Sem as colunas na dim (db/input/2026_09_30_ddl_coordenadas_dim.sql ainda não rodou), avisa e não faz nada."""
    dim = f.dim_fazendas
    if dim.empty or not all(c in dim.columns for c in COLS_COORD_DIM):
        print(f"   ⚠️ {TAB_DIM_FAZENDAS} sem as colunas de coordenada: rode db/input/2026_09_30_ddl_coordenadas_dim.sql e execute de novo.")
        return 0
    novos = localizacao.set_index("codigo_lr")
    leite = dim[dim["tipo_ponto_atendimento"].astype(str).str.upper() == "LEITE"]
    alterar = []
    for r in leite.to_dict("records"):
        cod = rn.limpar_codigo_lr(r.get("codigo_produtor"))
        if cod not in novos.index:
            continue
        n = novos.loc[cod]
        lat, lon = _numero(n["latitude"]), _numero(n["longitude"])
        origem, corrigida = n["origem_coordenada"], bool(n["coordenada_corrigida"])
        mudou = (_numero(r.get("latitude")) != lat or _numero(r.get("longitude")) != lon
                 or r.get("origem_coordenada") != origem or _verdadeiro(r.get("coordenada_corrigida")) != corrigida)
        if mudou:
            alterar.append({**r, "latitude": lat, "longitude": lon, "origem_coordenada": origem, "coordenada_corrigida": corrigida})
    registros = _registros_json(pd.DataFrame(alterar), {"mes_referencia"}, set()) if alterar else []
    for i in range(0, len(registros), LOTE):
        supabase.table(TAB_DIM_FAZENDAS).upsert(registros[i:i + LOTE], on_conflict="id").execute()
    print(f"   ✅ {TAB_DIM_FAZENDAS}: coordenada gravada em {len(registros)} linhas ({len(leite) - len(registros)} já estavam atualizadas).")
    return len(registros)


def exportar_localizacao(localizacao: pd.DataFrame, raiz: Path) -> Path | None:
    """Fazendas sem coordenada própria válida ou com divergência entre fontes, para corrigir o cadastro na origem."""
    pendentes = localizacao[localizacao["observacao"].notna()]
    if pendentes.empty:
        return None
    pasta = raiz / "db" / "output" / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{datetime.now():%Y_%m_%d_%H%M%S}_localizacao_fazendas_conferencia.xlsx"
    resumo = localizacao.groupby(["agroindustria", "origem_coordenada"], dropna=False).size().reset_index(name="fazendas")
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        pendentes.sort_values(["agroindustria", "origem_coordenada", "codigo_lr"]).to_excel(w, sheet_name="Conferir", index=False)
        resumo.to_excel(w, sheet_name="Resumo", index=False)
    return caminho


# ─── Gravação idempotente ─────────────────────────────────────────────────────

COLUNAS_INTEIRAS = {
    "id_atendimento", "vacas_lactacao", "vacas_secas", "bezerras_aleitamento",
    "bezerros_aleitamento", "novilhas", "reprodutores", "receptoras",
    "rebanho_total", "dados_elabore_pct",
    "id_consultor", "id_agroindustria", "id_regiao", "qtd_fazendas", "qtd_visitas", "qtd_movimentacoes",
    "n_visitas_mes", "n_plantios", "n_producoes", "producoes_inconsistentes",
}


def _registros_json(df: pd.DataFrame, colunas_data: set[str], colunas_ts: set[str]) -> list[dict]:
    registros = []
    for r in df.to_dict("records"):
        limpo = {}
        for k, v in r.items():
            if isinstance(v, dict):
                limpo[k] = v
            elif k in colunas_data:
                limpo[k] = _iso_data(v)
            elif k in colunas_ts:
                limpo[k] = _iso_ts(v)
            elif v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) or (not isinstance(v, (list, dict)) and pd.isna(v)):
                limpo[k] = None
            elif k in COLUNAS_INTEIRAS:
                try:
                    limpo[k] = int(round(float(v)))
                except (ValueError, TypeError):
                    limpo[k] = None
            elif hasattr(v, "item"):
                limpo[k] = v.item()
            else:
                limpo[k] = v
        registros.append(limpo)
    return registros


def sincronizar_tabela(supabase, tabela: str, registros: list[dict], chave: str = "id_composto") -> None:
    """Upsert de todas as linhas e remoção das chaves que não existem mais na origem."""
    agora = datetime.now(FUSO_SP).isoformat()
    for r in registros:
        r["data_processamento"] = agora
    for i in range(0, len(registros), LOTE):
        supabase.table(tabela).upsert(registros[i:i + LOTE], on_conflict=chave).execute()
    if "," in chave:
        print(f"   ✅ {tabela}: {len(registros)} linhas publicadas (on_conflict={chave}).")
        return
    novas = {r[chave] for r in registros}
    existentes = set(_buscar_tudo(supabase, tabela, chave).get(chave, pd.Series(dtype=str)).dropna())
    obsoletas = sorted(existentes - novas)
    for i in range(0, len(obsoletas), 200):
        supabase.table(tabela).delete().in_(chave, obsoletas[i:i + 200]).execute()
    print(f"   ✅ {tabela}: {len(registros)} linhas publicadas, {len(obsoletas)} obsoletas removidas.")


def validar_cobertura_raw_visitas(raw: pd.DataFrame, corte_estatico: str, inicio_dinamico: str) -> None:
    """A raw precisa conter a base estática e a dinâmica (LISTA_GERAL_VISITAS.xlsx)."""
    datas = _data(raw["data_visita"])
    estatica = int((datas <= pd.Timestamp(corte_estatico) + pd.Timedelta(days=1)).sum())
    dinamica = int((datas >= pd.Timestamp(inicio_dinamico)).sum())
    print(f"🔎 Cobertura da raw: estática até {corte_estatico} = {estatica} | dinâmica desde {inicio_dinamico} = {dinamica} "
          f"| última visita = {datas.max():%Y-%m-%d}")
    if dinamica == 0:
        raise RuntimeError(
            f"sq_raw_visitas não tem visitas a partir de {inicio_dinamico}. Rode antes "
            "scripts/functions/carregar_historico_visitas.py (carrega LISTA_GERAL_VISITAS.xlsx)."
        )


def validar_queda_visitas(supabase, novas: pd.DataFrame) -> None:
    """Impede que uma raw incompleta derrube meses inteiros da fato publicada por esta etapa.

    Só compara com linhas já publicadas pela camada de consumo (agroindustria preenchida): a
    primeira publicação remove de propósito as visitas fora da cadeia de leite da carga antiga.
    """
    atuais = _buscar_tudo(supabase, TAB_VISITAS, "mes_referencia,agroindustria")
    atuais = atuais[atuais["agroindustria"].notna()] if not atuais.empty else atuais
    if atuais.empty:
        print("   ℹ️ Primeira publicação da camada de consumo: comparação de volume por mês ignorada.")
        return
    antes = _mes(atuais["mes_referencia"]).value_counts()
    depois = novas["mes_referencia"].value_counts() if not novas.empty else pd.Series(dtype=int)
    for mes, qtd_antes in antes.items():
        qtd_depois = int(depois.get(mes, 0))
        if qtd_antes >= 50 and qtd_depois < qtd_antes * (1 - TOLERANCIA_QUEDA_VISITAS_MES):
            raise RuntimeError(
                f"Carga abortada: {mes:%Y-%m} cairia de {qtd_antes} para {qtd_depois} visitas. "
                "Verifique se sq_raw_visitas foi carregada por completo antes de publicar."
            )


# ─── Dimensões relacionadas: consultor × agroindústria × região ────────────────

def consultores_das_fatos(r: dict) -> list[str]:
    """Nomes de consultor gravados nas fatos (exceto 'NÃO ATRIBUÍDO', que não é uma pessoa)."""
    nomes: set[str] = set()
    for fato, coluna in (("carteira", "consultor"), ("visitas", "nome_consultor"),
                         ("movimentacao", "consultor"), ("consistencia", "nome_consultor")):
        df = r.get(fato)
        if df is not None and not df.empty and coluna in df.columns:
            nomes.update(n for n in (_texto(x) for x in df[coluna].dropna().unique()) if n)
    return sorted(n for n in nomes if n != rn.NAO_ATRIBUIDO)


def sincronizar_dim_consultor(supabase, dim: pd.DataFrame, nomes: list[str]) -> dict[str, int]:
    """Completa tab_dim_consultor e devolve chave_consultor -> id.

    O cadastro veio só do Elabore: consultores do SmartQuestion/Procampo não existiam nele e a dimensão não
    conseguia se relacionar com as fatos. Preenche chave_consultor dos cadastros sem chave e cria os que faltam
    (origem = 'BI')."""
    por_chave: dict[str, int] = {}
    if not dim.empty:
        for r in dim.to_dict("records"):
            k = _texto(r.get("chave_consultor"))
            if k:
                por_chave[k] = int(r["id"])
        for r in dim.to_dict("records"):
            nome = _texto(r.get("nome_consultor"))
            k = rn.chave(nome) if nome else ""
            if _texto(r.get("chave_consultor")) or not k or k.startswith("CONSULTOR LABOR RURAL") or k in por_chave:
                continue
            supabase.table(TAB_DIM_CONSULTOR).update({"chave_consultor": k}).eq("id", int(r["id"])).execute()
            por_chave[k] = int(r["id"])
    novos = sorted({rn.chave(n): n for n in nomes if rn.chave(n) not in por_chave}.items())
    if novos:
        supabase.table(TAB_DIM_CONSULTOR).insert(
            [{"nome_consultor": nome, "chave_consultor": k, "origem": "BI", "status": "Ativo", "excluido": 0}
             for k, nome in novos]).execute()
        atualizada = _buscar_tudo(supabase, TAB_DIM_CONSULTOR, "id,chave_consultor", ordem="id")
        por_chave = {k: int(i) for k, i in zip(atualizada["chave_consultor"], atualizada["id"]) if _texto(k)}
        print(f"   ➕ {TAB_DIM_CONSULTOR}: {len(novos)} consultores criados (só existiam no SmartQuestion/Procampo).")
    return por_chave


def construir_consultor_atuacao(ctx: Contexto, r: dict, ids_consultor: dict[str, int]) -> tuple[pd.DataFrame, int]:
    """Ponte consultor × agroindústria × região × mês, a partir das fatos já resolvidas.

    É o relacionamento que os filtros do dashboard usam: um consultor só aparece para as agroindústrias e regiões
    em que realmente atua. Devolve (ponte, linhas ignoradas por consultor/agroindústria fora da dimensão)."""
    acumulado: dict[tuple, dict] = {}

    def somar(df, coluna_consultor, campo, coluna_mes=None, distinto=None):
        if df is None or df.empty:
            return
        base = df.assign(_mes=_mes(df[coluna_mes]) if coluna_mes else df["mes_referencia"])
        grupos = base.groupby(["_mes", coluna_consultor, "agroindustria", "regiao"], dropna=True)
        contagem = grupos[distinto].nunique() if distinto else grupos.size()
        for (mes, consultor, agro, regiao), qtd in contagem.items():
            linha = acumulado.setdefault((mes, consultor, agro, regiao),
                                         {"qtd_fazendas": 0, "qtd_visitas": 0, "qtd_movimentacoes": 0})
            linha[campo] = int(qtd)

    somar(r.get("carteira"), "consultor", "qtd_fazendas", distinto="codigo_lr")
    somar(r.get("visitas"), "nome_consultor", "qtd_visitas")
    somar(r.get("movimentacao"), "consultor", "qtd_movimentacoes", coluna_mes="data_movimentacao")

    # O dashboard só oferece os meses da carteira: visitas/movimentações anteriores (ou com data inválida,
    # ex.: 1970-01-01) não entram na ponte, que a API devolve inteira a cada requisição.
    carteira = r.get("carteira")
    piso = carteira["mes_referencia"].min() if carteira is not None and not carteira.empty else pd.NaT
    linhas: dict[str, dict] = {}
    ignoradas = 0
    for (mes, consultor, agro, regiao), q in acumulado.items():
        if pd.isna(mes) or not consultor or consultor == rn.NAO_ATRIBUIDO or (pd.notna(piso) and mes < piso):
            continue
        id_consultor = ids_consultor.get(rn.chave(consultor))
        dim_agro = ctx.agro_dim.get(rn.chave(agro))
        if id_consultor is None or dim_agro is None:
            ignoradas += 1
            continue
        id_regiao = ctx.regiao_ids.get((rn.chave(agro), rn.chave(regiao)))
        id_composto = f"{mes:%Y-%m-%d}|{id_consultor}|{dim_agro['id']}|{id_regiao or 0}"
        atual = linhas.get(id_composto)
        if atual:   # duas grafias do mesmo consultor (mesma chave) somam na mesma linha
            for campo in q:
                atual[campo] += q[campo]
            continue
        linhas[id_composto] = {
            "id_composto": id_composto, "mes_referencia": mes, "id_consultor": id_consultor,
            "id_agroindustria": int(dim_agro["id"]), "id_regiao": id_regiao,
            "consultor": consultor, "agroindustria": dim_agro.get("nome_bi") or agro, "regiao": regiao, **q,
        }
    return pd.DataFrame(list(linhas.values())), ignoradas


def alinhar_fato_economico(supabase, ctx: Contexto, gravar: bool) -> int:
    """Dá a tab_fato_economico a mesma agroindústria/região canônicas das demais fatos.

    carregar_fato_economico.py grava o texto bruto da planilha do Elabore ('NESTLÉ', 'LABOR RURAL'...), que não
    casa com nenhuma dimensão: o filtro de região do dashboard ficava sem relação nas telas econômicas."""
    eco = _buscar_tudo(supabase, TAB_ECONOMICO, "id_composto,codigo_lr,mes_referencia,agroindustria,regiao", ordem="id_composto")
    if eco.empty:
        return 0
    eco["_mes"] = _mes(eco["mes_referencia"])
    grupos: dict[tuple, list[str]] = {}
    for r in eco.to_dict("records"):
        cod = rn.limpar_codigo_lr(r.get("codigo_lr"))
        mes = r["_mes"]
        projeto = ctx.projeto(cod, mes)
        agro = ctx.agroindustria(cod, projeto or rn.mapear_agroindustria(r.get("agroindustria")), mes)
        if rn.chave(agro) not in ctx.agro_dim:   # contas internas/teste ("LATICÍNIOS LABOR RURAL"): fora das dimensões
            agro = rn.AGRO_NAO_INFORMADA
        regiao = ctx.regiao_de(cod, agro, projeto, mes)
        if agro != r.get("agroindustria") or regiao != r.get("regiao"):
            grupos.setdefault((agro, regiao), []).append(r["id_composto"])
    total = sum(len(v) for v in grupos.values())
    sem_regiao = sum(len(v) for (_, regiao), v in grupos.items() if regiao == rn.REGIAO_NAO_INFORMADA)
    print(f"   🔗 {TAB_ECONOMICO}: {total} de {len(eco)} linhas com agroindústria/região fora do padrão canônico "
          f"({sem_regiao} ficarão com região NÃO INFORMADA).")
    if gravar:
        for (agro, regiao), ids in grupos.items():
            for i in range(0, len(ids), 200):
                supabase.table(TAB_ECONOMICO).update({"agroindustria": agro, "regiao": regiao}) \
                    .in_("id_composto", ids[i:i + 200]).execute()
        print(f"   ✅ {TAB_ECONOMICO}: agroindústria/região alinhadas com as dimensões.")
    return total


def pares_fora_da_dimensao(ctx: Contexto, r: dict) -> pd.DataFrame:
    """Pares agroindústria × região gravados nas fatos que não existem em tab_dim_regiao (esperado: nenhum).

    'NÃO INFORMADA' é o único valor de região aceito fora da dimensão."""
    achados = []
    for fato in ("carteira", "visitas", "movimentacao", "consistencia", "producao", "safra"):
        df = r.get(fato)
        if df is None or df.empty:
            continue
        pares = df[["agroindustria", "regiao"]].drop_duplicates()
        for agro, regiao in pares.itertuples(index=False):
            if rn.chave(regiao) == rn.chave(rn.REGIAO_NAO_INFORMADA):
                continue
            if not ctx.regiao.valida(agro, regiao):
                achados.append({"fato": fato, "agroindustria": agro, "regiao": regiao})
    return pd.DataFrame(achados, columns=["fato", "agroindustria", "regiao"])


def exportar_divergencias(ctx: Contexto, raiz: Path) -> Path | None:
    """Relatório das fazendas cuja região/agroindústria não fecha com as dimensões (para corrigir na origem)."""
    if not ctx.divergencias:
        return None
    pasta = raiz / "db" / "output" / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{datetime.now():%Y_%m_%d_%H%M%S}_divergencias_dimensoes.xlsx"
    df = pd.DataFrame(list(ctx.divergencias.values())).sort_values(["tipo", "agroindustria", "codigo_lr"], na_position="last")
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Detalhamento", index=False)
        df.groupby("tipo").agg(fazendas=("codigo_lr", "nunique")).reset_index().to_excel(w, sheet_name="Resumo", index=False)
    return caminho


# ─── Orquestração ─────────────────────────────────────────────────────────────

COLS_VISITAS = ["id_composto", "id_atendimento", "codigo_lr", "nome_consultor", "profissao_consultor", "mes_referencia",
                "data_visita", "tipo_visita", "nome_produtor", "nome_propriedade", "projeto", "agroindustria", "regiao",
                "codigo_agroindustria", "id_farm", "valor_pago_produtor", "valor_pago_agroindustria",
                "area_pecuaria_ha", "mdo_dias_homem", "producao_l_dia", "ccs_mensal", "cpp_mensal", "gordura_mensal",
                "proteina_mensal", "vacas_lactacao", "vacas_secas", "bezerras_aleitamento", "bezerros_aleitamento",
                "novilhas", "reprodutores", "receptoras", "rebanho_total", "status_produtor",
                "origem_dados", "cadastro_elabore", "dados_elabore_pct", "dados_elabore_status", "blocos_elabore"]

COLS_CONSISTENCIA = ["id_composto", "codigo_lr", "nome_consultor", "profissao_consultor", "projeto", "mes_referencia",
                     "data_carencia_fim", "mes_elabore", "consistencia_mensal", "consistencia_anual",
                     "status_code", "excecao", "sem_lancamento_elabore", "meses_sequenciais", "detalhamento_inconsistencia",
                     "agroindustria", "regiao", "na_carteira", "detalhamento_anual",
                     "cadastro_elabore", "dados_elabore_pct", "dados_elabore_status", "blocos_elabore"]

COLS_MOVIMENTACAO = ["id_composto", "codigo_lr", "nome_produtor", "numero_atendimento", "data_movimentacao",
                     "data_solicitacao", "consultor", "tipo", "motivo", "projeto", "agroindustria", "regiao"]


def construir_tudo(f: Fontes, agora: datetime | None = None, data_inicial_consistencia: str = "2025-01-01") -> dict:
    ctx = Contexto(f, agora or datetime.now(FUSO_SP).replace(tzinfo=None))
    visitas, auditoria = construir_fato_visitas(f, ctx)
    carteira = construir_carteira_mensal(f, ctx, visitas)
    consistencia = construir_fato_consistencia(f, ctx, carteira, data_inicial_consistencia)
    movimentacao = construir_fato_movimentacao(f, ctx)
    producao = construir_producao_mensal(visitas, f.cfg_producao)
    localizacao = construir_localizacao(f, carteira)
    safra, resumo_safra = construir_safra_forrageira(f, carteira)
    return {"visitas": visitas, "auditoria": auditoria, "carteira": carteira,
            "consistencia": consistencia, "movimentacao": movimentacao,
            "producao": producao, "localizacao": localizacao, "safra": safra, "resumo_safra": resumo_safra,
            "ctx": ctx}


def exportar_auditoria(auditoria: pd.DataFrame, raiz: Path) -> Path:
    pasta = raiz / "db" / "output" / "logs"
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"{datetime.now():%Y_%m_%d_%H%M%S}_reconciliacao_visitas_detalhada.xlsx"
    with pd.ExcelWriter(caminho, engine="openpyxl") as w:
        auditoria.to_excel(w, sheet_name="Detalhamento", index=False)
        auditoria.groupby(["status_reconciliacao", "tipo_visita"], dropna=False).size() \
            .reset_index(name="quantidade").to_excel(w, sheet_name="Resumo_Por_Status", index=False)
    return caminho


def executar_camada_consumo(raiz: Path | None = None, gravar: bool = True) -> dict:
    import psycopg2
    from functions.function import carregar_config_referencia, carregar_env, obter_cliente_supabase
    import yaml

    raiz = raiz or raiz_projeto
    carregar_env(raiz)
    carregar_config_referencia(raiz)
    with open(raiz / "scripts" / "config" / "config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    ref = cfg["referencia"]

    print("=" * 70)
    print("🧱 CAMADA DE CONSUMO DO DASHBOARD")
    print("=" * 70)
    supabase = obter_cliente_supabase(raiz)
    conexao = psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.getenv("PG_PORT", "5432")), dbname=os.getenv("PG_DATABASE", "postgres"),
        user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], sslmode="require", connect_timeout=15,
    )
    try:
        fontes = carregar_fontes(supabase, conexao, ref["data_inicial_elabore"])
    finally:
        conexao.close()
    fontes.cfg_procampo = cfg.get("procampo", {})
    fontes.cfg_producao = cfg.get("producao", {})
    fontes.cfg_safra = cfg.get("safra_forrageira", {})

    validar_cobertura_raw_visitas(fontes.raw_visitas, ref["data_corte_estatica_visitas"], ref["data_inicial_fato_visitas"])
    r = construir_tudo(fontes, data_inicial_consistencia=ref["data_inicial_analise"])
    auditoria = r["auditoria"]
    print("\n📊 Resultado:")
    print(f"   -> visitas válidas: {len(r['visitas'])} de {len(auditoria)} na raw (SmartQuestion + Procampo)")
    print(auditoria["status_reconciliacao"].value_counts().to_string())
    if not r["visitas"].empty and "origem_dados" in r["visitas"].columns:
        print(f"   -> visitas do Procampo na fato: {int((r['visitas']['origem_dados'] == 'PROCAMPO').sum())}")
    print(f"   -> carteira (fazenda×mês×consultor): {len(r['carteira'])}")
    print(f"   -> consistência (fazenda×mês): {len(r['consistencia'])}")
    print(f"   -> movimentações de leite: {len(r['movimentacao'])}")
    prod, loc = r["producao"], r["localizacao"]
    print(f"   -> produção (fazenda×mês com medição): {len(prod)} | suspeitas: {int(prod['producao_suspeita'].sum())} "
          f"(imputadas: {int(prod['producao_imputada'].sum())}) | com qualidade descartada: "
          f"{int(prod['qualidade_descartada'].sum())}")
    print(prod.groupby("agroindustria").agg(linhas=("codigo_lr", "size"), fazendas=("codigo_lr", "nunique"),
                                            com_ccs=("ccs", "count")).to_string())
    print(f"   -> localização: {len(loc)} fazendas | " + " | ".join(
        f"{k}: {n}" for k, n in loc["origem_coordenada"].value_counts().items()) +
        f" | corrigidas: {int(loc['coordenada_corrigida'].sum())}")
    safra, rs = r["safra"], r["resumo_safra"]
    if safra.empty:
        print("   -> safra: sem dados nas raws de safra (carregar_safra_forrageira.py pendente?)")
    else:
        print(f"   -> safra: {len(safra)} linhas ({int((safra['bloco'] == 'AREA').sum())} de área, "
              f"{int((safra['bloco'] == 'CUSTO').sum())} de custo) | fazendas: {safra['codigo_lr'].nunique()} | "
              f"fora da carteira de leite (não entram): {rs['sem_carteira']} | linhas sem código LR: {rs['linhas_sem_codigo']}")
        print(safra.groupby(["agroindustria", "safra"]).agg(fazendas=("codigo_lr", "nunique")).unstack(fill_value=0).to_string())
        if rs["codigos_sem_carteira"]:
            print(f"      fora da carteira (ex.): {', '.join(rs['codigos_sem_carteira'][:15])}")
    caminho_loc = exportar_localizacao(loc, raiz)
    print(f"   📄 Conferência de localização: {caminho_loc or 'nenhuma pendência'}")
    print(f"   📄 Auditoria: {exportar_auditoria(auditoria, raiz)}")

    ctx = r["ctx"]
    fora = pares_fora_da_dimensao(ctx, r)
    print(f"   🔗 pares agroindústria×região fora de tab_dim_regiao nas fatos: {len(fora)} (esperado: 0)")
    if not fora.empty:
        print(fora.to_string(index=False))

    if not gravar:
        alinhar_fato_economico(supabase, ctx, gravar=False)
        chaves_dim = set(fontes.dim_consultor["chave_consultor"].dropna()) if "chave_consultor" in fontes.dim_consultor else set()
        faltam = [n for n in consultores_das_fatos(r) if rn.chave(n) not in chaves_dim]
        print(f"   🔗 consultores das fatos sem chave em tab_dim_consultor (serão criados na carga real): {len(faltam)}")
        caminho = exportar_divergencias(ctx, raiz)
        print(f"   📄 Divergências fatos × dimensões: {caminho or 'nenhuma'} ({len(ctx.divergencias)} ocorrências)")
        print("\n🧪 Dry-run: nada foi gravado no Supabase.")
        return r

    validar_queda_visitas(supabase, r["visitas"])
    print("\n💾 Publicando camada de consumo...")
    d, ts = {"mes_referencia", "data_associacao", "data_ultima_visita", "data_visita_mes_anterior", "mes_elabore",
             "data_carencia_fim", "data_movimentacao"}, {"data_visita"}
    sincronizar_tabela(supabase, TAB_VISITAS, _registros_json(r["visitas"][COLS_VISITAS], d, ts))
    sincronizar_tabela(supabase, TAB_CARTEIRA, _registros_json(r["carteira"], d, ts))
    cols_cons = [c for c in COLS_CONSISTENCIA if c in r["consistencia"].columns]
    # A grain de tab_fato_consistencia é fazenda×mês e já vem materializada em id_composto
    # (f"{cod}|{mes}"), que é a PK da tabela. O on_conflict pelo trio
    # (codigo_lr, nome_consultor, mes_referencia) não tem unique constraint correspondente
    # e fazia o Postgres recusar todo o upsert com 42P10, deixando a tabela vazia.
    sincronizar_tabela(supabase, TAB_CONSISTENCIA, _registros_json(r["consistencia"][cols_cons], d, ts))
    cols_mov = [c for c in COLS_MOVIMENTACAO if c in r["movimentacao"].columns]
    sincronizar_tabela(supabase, TAB_MOVIMENTACAO, _registros_json(r["movimentacao"][cols_mov], d, ts))
    sincronizar_tabela(supabase, TAB_PRODUCAO, _registros_json(r["producao"][COLS_PRODUCAO], d, ts))
    if r["safra"].empty:
        # Raw vazia/indisponível não pode apagar a fato já publicada.
        print(f"   ⚠️ {TAB_SAFRA} não foi atualizada: raws de safra vazias.")
    else:
        sincronizar_tabela(supabase, TAB_SAFRA, _registros_json(r["safra"], d, ts))
    publicar_coordenadas_dim(supabase, fontes, r["localizacao"])

    print("\n🔗 Relacionando dimensões (consultor × agroindústria × região)...")
    ids_consultor = sincronizar_dim_consultor(supabase, fontes.dim_consultor, consultores_das_fatos(r))
    ponte, ignoradas = construir_consultor_atuacao(ctx, r, ids_consultor)
    if ponte.empty:
        print(f"   ⚠️ {TAB_CONSULTOR_ATUACAO} não foi atualizada: nenhuma linha gerada.")
    else:
        sincronizar_tabela(supabase, TAB_CONSULTOR_ATUACAO, _registros_json(ponte, d, ts))
        if ignoradas:
            print(f"   ⚠️ {ignoradas} combinações ficaram fora da ponte (consultor ou agroindústria fora das dimensões).")
    alinhar_fato_economico(supabase, ctx, gravar=True)
    caminho = exportar_divergencias(ctx, raiz)
    print(f"   📄 Divergências fatos × dimensões: {caminho or 'nenhuma'} ({len(ctx.divergencias)} ocorrências)")
    print("🎉 Camada de consumo publicada.")
    return r


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Calcula e exporta a auditoria sem gravar no Supabase")
    args = parser.parse_args()
    executar_camada_consumo(gravar=not args.dry_run)
