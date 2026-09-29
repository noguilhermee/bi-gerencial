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
from dataclasses import dataclass
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
    elabore_propriedades: pd.DataFrame
    elabore_blocos: pd.DataFrame


def _buscar_tudo(supabase, tabela: str, colunas: str = "*") -> pd.DataFrame:
    registros, inicio = [], 0
    while True:
        dados = supabase.table(tabela).select(colunas).range(inicio, inicio + LOTE - 1).execute().data or []
        registros.extend(dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    return pd.DataFrame(registros)


SQL_ELABORE_PROPRIEDADES = """
SELECT upper(trim(labor_rural_code)) AS codigo_lr, dairy_region, agroindustry_name, property_status
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
                                           "codigo_produtor,grupo_ponto_atendimento,nome_grupo_ponto_atendimento"),
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
        "dim_consultor": _buscar_tudo(supabase, "sq_dim_consultor", "nome_consultor,formacao_consultor,excluido"),
        "dim_regiao": _buscar_tudo(supabase, "sq_dim_regiao", "nome_regiao,nome_regiao_formatada,agroindustria,status"),
    }
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
        self.regiao = rn.ResolvedorRegiao(f.dim_regiao.to_dict("records"))

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
        self.regiao_elabore = {k: v for k, v in self.regiao_elabore.items() if v != "NÃO INFORMADA"}
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

    def agroindustria(self, codigo_lr: str, projeto, mes=None) -> str:
        faz = self.fazenda(codigo_lr, mes)
        return rn.mapear_agroindustria(_texto(faz.get("agroindustria")) or projeto)

    def regiao_de(self, codigo_lr: str, agroindustria: str, projeto, mes=None) -> str:
        if codigo_lr in self.regiao_elabore:
            return self.regiao_elabore[codigo_lr]
        faz = self.fazenda(codigo_lr, mes)
        return self.regiao.resolver(_texto(faz.get("regiao")) or _texto(faz.get("unidade_atendimento")), agroindustria, projeto)

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
    return pd.DataFrame(linhas), pd.DataFrame(auditoria)


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
        grupos_lista_geral.setdefault(cod, []).extend([r.get("nome_grupo_ponto_atendimento"), r.get("grupo_ponto_atendimento")])

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
    return pd.DataFrame(linhas)


# ─── Gravação idempotente ─────────────────────────────────────────────────────

COLUNAS_INTEIRAS = {
    "id_atendimento", "vacas_lactacao", "vacas_secas", "bezerras_aleitamento",
    "bezerros_aleitamento", "novilhas", "reprodutores", "receptoras",
    "rebanho_total", "dados_elabore_pct",
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
                     "status_code", "excecao", "meses_sequenciais", "detalhamento_inconsistencia",
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
    return {"visitas": visitas, "auditoria": auditoria, "carteira": carteira,
            "consistencia": consistencia, "movimentacao": movimentacao}


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

    validar_cobertura_raw_visitas(fontes.raw_visitas, ref["data_corte_estatica_visitas"], ref["data_inicial_fato_visitas"])
    r = construir_tudo(fontes, data_inicial_consistencia=ref["data_inicial_analise"])
    auditoria = r["auditoria"]
    print("\n📊 Resultado:")
    print(f"   -> visitas válidas: {len(r['visitas'])} de {len(auditoria)} na raw")
    print(auditoria["status_reconciliacao"].value_counts().to_string())
    print(f"   -> carteira (fazenda×mês×consultor): {len(r['carteira'])}")
    print(f"   -> consistência (fazenda×mês): {len(r['consistencia'])}")
    print(f"   -> movimentações de leite: {len(r['movimentacao'])}")
    print(f"   📄 Auditoria: {exportar_auditoria(auditoria, raiz)}")

    if not gravar:
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
    print("🎉 Camada de consumo publicada.")
    return r


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Calcula e exporta a auditoria sem gravar no Supabase")
    args = parser.parse_args()
    executar_camada_consumo(gravar=not args.dry_run)
