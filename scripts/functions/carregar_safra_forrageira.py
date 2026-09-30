# -*- coding: utf-8 -*-
"""
Script: carregar_safra_forrageira.py
Objetivo: Carregar a camada raw dos indicadores de safra (slide 7 do BI) a partir da base de custo de forrageira
gerada pelo lr-indicadores-elabore (notebook "Elabore Indicadores", seção 5):

  elabore_raw_custo_forrageira    <- aba "A - Custo por Produção": 1 linha por produção (id_production), com área
                                     colhida, quantidade, custo por etapa (nominal e deflacionado), sistema de plantio
                                     e as marcações de consistência da origem.
  elabore_raw_sistema_cobertura   <- aba "Sistema e Cobertura": 1 linha por plantio (Elabore + complemento do Plano
                                     Agronômico), com área plantada, sistema de plantio e se o talhão recebeu cobertura
                                     ou cultivo em sucessão.

Lê o arquivo *_base_custo_forrageira.xlsx mais recente de caminhos.elabore_forrageira (config.yaml). Sem regra de
negócio aqui (padrão das *_raw_*): só renomeia colunas e converte tipos. A raw espelha a planilha mais recente
(snapshot completo): upsert pela chave e remoção das linhas que deixaram de existir, com trava contra planilha
incompleta (safra_forrageira.limite_remocao_pct).

Uso:
  python scripts/functions/carregar_safra_forrageira.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

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

from functions.function import carregar_env, obter_cliente_supabase  # noqa: E402
from functions.carregar_procampo import (  # noqa: E402
    _conferir, _enviar, _ler_excel, _remover_obsoletas, carregar_configuracao,
)

ABA_CUSTO = "A - Custo por Produção"
ABA_SISTEMA = "Sistema e Cobertura"
PADRAO_ARQUIVO = "*_base_custo_forrageira.xlsx"

# Planilha -> raw. Só entram as colunas que a camada de consumo usa ou que servem de rastreio.
COLUNAS_CUSTO = {
    "id_production": "id_production",
    "id_planted_culture": "id_planted_culture",
    "Código LR": "codigo_lr",
    "Fazenda": "nome_propriedade",
    "Produtor": "nome_produtor",
    "Consultor": "consultor_elabore",
    "Área": "area",
    "Cultura": "cultura",
    "Safra": "safra",
    "Ciclo (produção)": "ciclo",
    "Produto Colhido": "produto_colhido",
    "Categoria": "categoria",
    "Data de Plantio": "data_plantio",
    "Data de Colheita": "data_colheita",
    "Área Colhida (ha)": "area_colhida_ha",
    "Área Plantada (ha)": "area_plantada_ha",
    "Quantidade (kg)": "quantidade_kg",
    "Pré-plantio (R$) (Nom)": "custo_pre_plantio_nom",
    "Plantio (R$) (Nom)": "custo_plantio_nom",
    "Tratos Culturais (R$) (Nom)": "custo_tratos_nom",
    "Colheita/Ensilagem Planta Inteira (R$) (Nom)": "custo_colheita_planta_inteira_nom",
    "Colheita/Ensilagem Grão (R$) (Nom)": "custo_colheita_grao_nom",
    "Etapa Não Informada (R$) (Nom)": "custo_etapa_nao_informada_nom",
    "Custo Total Rateado (R$) (Nom)": "custo_total_nom",
    "Custo Total Rateado (R$) (Defl)": "custo_total_defl",
    "Plantio Direto": "plantio_direto",
    "Sistema de Plantio": "sistema_plantio",
    "Origem do Sistema de Plantio": "origem_sistema_plantio",
    "Sem Quantidade Produzida": "sem_quantidade_produzida",
    "Ciclo Divergente": "ciclo_divergente",
    "Alerta Custo/ha": "alerta_custo_ha",
    "Alerta Colheita Sem Par": "alerta_colheita_sem_par",
}

COLUNAS_SISTEMA = {
    "id_plantio": "id_plantio",
    "id_talhao": "id_talhao",
    "Fonte": "fonte",
    "Código LR": "codigo_lr",
    "Fazenda": "nome_propriedade",
    "Produtor": "nome_produtor",
    "Consultor": "consultor_elabore",
    "Área": "area",
    "Cultura": "cultura",
    "Safra": "safra",
    "Produção / Ciclo": "ciclo",
    "Data de Plantio": "data_plantio",
    "Área Plantada (ha)": "area_plantada_ha",
    "Sistema de Plantio": "sistema_plantio",
    "Origem do Sistema de Plantio": "origem_sistema_plantio",
    "Planta de Cobertura": "planta_cobertura",
    "Cobertura Após": "cobertura_apos",
    "Cultivo em Sucessão": "sucessao_apos",
    "Cultura Seguinte": "cultura_seguinte",
}

NUMERICAS = {"area_colhida_ha", "area_plantada_ha", "quantidade_kg", "custo_pre_plantio_nom", "custo_plantio_nom",
             "custo_tratos_nom", "custo_colheita_planta_inteira_nom", "custo_colheita_grao_nom",
             "custo_etapa_nao_informada_nom", "custo_total_nom", "custo_total_defl"}
BOOLEANAS = {"plantio_direto", "sem_quantidade_produzida", "ciclo_divergente", "alerta_custo_ha",
             "planta_cobertura", "cobertura_apos", "sucessao_apos"}
DATAS = {"data_plantio", "data_colheita"}


def _chave_aba(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def resolver_aba(base: Path, aba: str) -> str:
    """Nome real da aba na planilha (ignora caixa, acento e espaços repetidos); lista as abas se não achar."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        shutil.copy2(base, tmp.name)
        with pd.ExcelFile(tmp.name) as xls:
            abas = list(xls.sheet_names)
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
    for nome in abas:
        if _chave_aba(nome) == _chave_aba(aba):
            return nome
    raise KeyError(f"Aba '{aba}' não encontrada em {base.name}. Abas existentes: {abas}. "
                   "Gere a base de novo pela seção 5 atualizada do notebook do lr-indicadores-elabore.")


def localizar_base(pasta: Path) -> Path:
    arquivos = sorted(pasta.glob(PADRAO_ARQUIVO))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum {PADRAO_ARQUIVO} em {pasta}. Rode a seção 5 do notebook do lr-indicadores-elabore.")
    return arquivos[-1]   # o prefixo AAAA_MM_DD_HHMMSS ordena por data de geração


def _booleano(valor):
    """'True'/'1'/'1.0' -> True; 'False'/'0' -> False; vazio ou 'Misto' -> None."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return None
    s = str(valor).strip().lower()
    if s in ("true", "1", "1.0", "sim", "verdadeiro"):
        return True
    if s in ("false", "0", "0.0", "nao", "não", "falso"):
        return False
    return None


def montar_raw(bruto: pd.DataFrame, colunas: dict, chave: str, arquivo: str) -> pd.DataFrame:
    faltando = [c for c in colunas if c not in bruto.columns]
    if faltando:
        raise KeyError(f"Colunas ausentes na planilha: {faltando}. A base precisa vir da seção 5 atualizada do notebook.")
    df = bruto[list(colunas)].rename(columns=colunas).copy()
    for c in df.columns:
        if c in NUMERICAS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        elif c in BOOLEANAS:
            df[c] = df[c].map(_booleano)
        elif c in DATAS:
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
        else:
            df[c] = df[c].where(df[c].notna(), None)
            df[c] = df[c].map(lambda v: None if v is None or str(v).strip().lower() in ("", "nan", "-") else str(v).strip())
    df["codigo_lr"] = df["codigo_lr"].map(lambda v: v.upper() if isinstance(v, str) else v)
    df["arquivo_origem"] = arquivo
    df = df[df[chave].notna()]
    if df[chave].duplicated().any():
        raise ValueError(f"Planilha com {chave} repetido ({int(df[chave].duplicated().sum())} linhas).")
    return df


def _para_json(df: pd.DataFrame) -> list[dict]:
    registros = []
    for r in df.to_dict("records"):
        limpo = {}
        for k, v in r.items():
            if v is None or (not isinstance(v, (str, bool)) and pd.isna(v)):
                limpo[k] = None
            elif isinstance(v, pd.Timestamp):
                limpo[k] = v.strftime("%Y-%m-%d")
            elif hasattr(v, "item"):
                limpo[k] = v.item()
            else:
                limpo[k] = v
        registros.append(limpo)
    return registros


def executar_carga_safra_forrageira(raiz: Path | None = None) -> dict:
    raiz = raiz or raiz_projeto
    print("=" * 70)
    print("🌽 CARGA DA RAW DE SAFRA (elabore_raw_custo_forrageira + elabore_raw_sistema_cobertura)")
    print("=" * 70)
    carregar_env(raiz)
    cfg = carregar_configuracao(raiz)
    tabelas = cfg["supabase"]["tabelas"]
    limite = float(cfg.get("safra_forrageira", {}).get("limite_remocao_pct", 0.2))
    base = localizar_base(Path(cfg["caminhos"]["elabore_forrageira"]))
    print(f"📥 Base: {base.name}")

    custo = montar_raw(_ler_excel(base, resolver_aba(base, ABA_CUSTO)), COLUNAS_CUSTO, "id_production", base.name)
    sistema = montar_raw(_ler_excel(base, resolver_aba(base, ABA_SISTEMA)), COLUNAS_SISTEMA, "id_plantio", base.name)
    print(f"   -> {ABA_CUSTO}: {len(custo)} produções | {ABA_SISTEMA}: {len(sistema)} plantios "
          f"({int((sistema['fonte'] == 'Elabore').sum())} Elabore, {int((sistema['fonte'] != 'Elabore').sum())} Plano Agronômico)")

    supabase = obter_cliente_supabase(raiz)
    for df, tabela, chave in ((custo, tabelas["safra_custo_raw"], "id_production"),
                              (sistema, tabelas["safra_sistema_raw"], "id_plantio")):
        print(f"⬆️ Enviando para {tabela}...")
        _enviar(supabase, tabela, _para_json(df), chave)
        _conferir(supabase, tabela, chave, set(df[chave]))
        _remover_obsoletas(supabase, tabela, chave, set(df[chave]), limite)
    print("=" * 70)
    return {"custo": custo, "sistema": sistema, "arquivo": base.name}


if __name__ == "__main__":
    executar_carga_safra_forrageira()
