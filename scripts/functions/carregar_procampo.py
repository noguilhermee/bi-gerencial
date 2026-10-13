# -*- coding: utf-8 -*-
"""
Script: carregar_procampo.py
Objetivo: Carregar a camada raw do Procampo (fazendas Piracanjuba fora do SmartQuestion):

  pro_raw_fazendas      <- BD_PROCAMPO/BASE_PRODUTORES_PROCAMPO_PIRACANJUBA.xlsx (abas ATIVOS + ROTATIVIDADE),
                           1 linha por matrícula. O Elabore (analytics_mart.vw_dim_property) só completa o
                           código: fazenda cadastrada usa o labor_rural_code dele (PRO#### ou LR######); fazenda
                           ainda fora do Elabore recebe o código PROVISÓRIO "MAT"+matrícula (config:
                           procampo.prefixo_codigo_provisorio). Cadastrar no Elabore é tarefa do consultor: quando
                           ele cadastrar, a carga seguinte troca o provisório pelo código real.
  pro_raw_movimentacao  <- as mesmas abas, linha a linha (entradas, saídas, trocas de consultor/titularidade).
  pro_raw_visitas       <- AAAA_MM_VISITAS_PIRACANJUBA_ESTEIO.xlsx: histórico em BD_PROCAMPO/VISITAS e o(s) arquivo(s)
                           do mês corrente em BD_PROCAMPO. A mesma visita pode estar em dois arquivos: vale a do mais
                           novo. A matrícula da visita liga à fazenda. Rascunhos ficam na raw (flag `rascunho`);
                           quem os descarta é a camada de consumo.

Sem regra de negócio aqui (padrão das *_raw_*): só renomeia colunas, converte tipos e resolve o código.
Upsert idempotente; nunca apaga (a raw preserva o histórico). CPF/CNPJ, e-mail e telefone da base não são carregados.

Uso:
  python scripts/functions/carregar_procampo.py
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
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

LOTE = 1000
COLUNAS_DATA = {"data_visita", "data_inicio", "data_conclusao", "mes_referencia"}

SQL_PROPRIEDADES_ELABORE = """
SELECT id_property,
       nullif(trim(agroindustry_code), '')      AS matricula,
       upper(nullif(trim(labor_rural_code), '')) AS codigo_elabore,
       entry_at                                  AS data_entrada_elabore
FROM analytics_mart.vw_dim_property
WHERE id_agroindustry::text = %(id_agro)s
  AND nullif(trim(agroindustry_code), '') IS NOT NULL
"""

# Colunas da base mestra (cabeçalho com espaços colapsados) -> colunas das raws
MAPA_BASE = {
    "PROPRIEDADE.CÓDIGO": "matricula",
    "PROPRIEDADE.FAZENDA.NOME": "nome_propriedade",
    "PROPRIEDADE.NOME": "nome_produtor",
    "LOCAL.UF": "uf",
    "LOCAL.MUNICÍPIO": "cidade",
    "PROPRIEDADE.REGIONAL": "regional",
    "LOCAL.LATITUDE": "latitude",
    "LOCAL.LONGITUDE": "longitude",
    "CONSULTOR.NOME": "consultor",
    "CONSULTORIA.NOME": "consultoria",
    "STATUS.PROJETO": "projeto_procampo",
    "DATA.INÍCIO": "data_inicio",
    "DATA.CONCLUSÃO": "data_conclusao",
    "STATUS.MOVIMENTAÇÃO": "status_movimentacao",
    "STATUS.JUSTIFICATIVA": "status_justificativa",
    '"MOTIVO" (SAÍDA)': "motivo_saida",   # só existe na aba ROTATIVIDADE
}
COLUNAS_FAZENDAS_RAW = ["matricula", "codigo_lr", "origem_codigo", "id_property", "no_elabore", "situacao", "aba_origem",
                        "nome_propriedade", "nome_produtor", "uf", "cidade", "regional", "latitude", "longitude",
                        "consultor", "consultoria", "projeto_procampo", "data_inicio", "data_conclusao",
                        "status_movimentacao", "motivo_saida", "data_entrada_elabore", "data_processamento"]
COLUNAS_MOVIMENTACAO_RAW = ["id_composto", "matricula", "codigo_lr", "aba_origem", "data_inicio", "data_conclusao",
                            "consultor", "consultoria", "projeto_procampo", "status_movimentacao",
                            "status_justificativa", "motivo_saida", "arquivo_origem", "data_processamento"]

# Colunas da planilha de visitas (após colapsar espaços repetidos) -> colunas da raw
MAPA_VISITAS = {
    "Matrícula": "matricula",
    "Fazenda": "nome_propriedade",
    "Produtor": "nome_produtor",
    "Consultor": "nome_consultor",
    "Empresa Responsável": "empresa_responsavel",
    "Tipo Visita": "tipo_visita",
    "Data da visita": "data_visita",
    "Horário início": "horario_inicio",
    "Horário fim": "horario_fim",
    "Rascunho": "rascunho",
    "Produção diária (litros/dia)": "producao_l_dia",
    "Vacas em lactação (número de vacas)": "vacas_lactacao",
    "CCS (x1.000 Cel/ml)": "ccs_mensal",
    "CPP (x1.000 UFC/ml)": "cpp_mensal",
    "Gordura (%)": "gordura_mensal",
    "Proteína (%)": "proteina_mensal",
    "Produtividade Média (litros/vaca/dia)": "produtividade_l_vaca_dia",
    "Seção Visitada": "secao_visitada",
    "Atividades Executadas": "atividades_executadas",
    "Recomendações ao Produtor": "recomendacoes",
    "Observações Adicionais": "observacoes",
    "Foto": "foto",
    "Assinatura": "assinatura",
    "Localização": "localizacao",
}
COLUNAS_NUMERICAS = ["producao_l_dia", "vacas_lactacao", "ccs_mensal", "cpp_mensal", "gordura_mensal",
                     "proteina_mensal", "produtividade_l_vaca_dia"]
COLUNAS_BOOLEANAS = ["rascunho", "foto", "assinatura"]
COLUNAS_VISITAS_RAW = ["id_composto", "matricula", "codigo_lr", "nome_propriedade", "nome_produtor", "nome_consultor",
                       "empresa_responsavel", "tipo_visita", "data_visita", "mes_referencia", "horario_inicio",
                       "horario_fim", "rascunho", *COLUNAS_NUMERICAS, "secao_visitada", "atividades_executadas", "recomendacoes",
                       "observacoes", "foto", "assinatura", "latitude", "longitude", "arquivo_origem",
                       "data_processamento"]


def carregar_configuracao(raiz: Path) -> dict:
    with open(raiz / "scripts" / "config" / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _limpar(valor):
    """Texto vazio, '-', 'nan' e afins viram None; o resto vem sem espaços nas pontas."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return None
    s = str(valor).replace("\xa0", " ").strip()
    return None if s.lower() in ("", "-", "nan", "none", "null") else s


def _numero(valor):
    """'-25,29922667' e '-25.29922667' -> float; o resto -> None."""
    s = _limpar(valor)
    if s is None:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _data(valor):
    """Data em texto ISO ou número serial do Excel ('46266' = 2026-09-01); o resto (ex.: 'Saiu') -> NaT."""
    s = _limpar(valor)
    if s is None:
        return pd.NaT
    if re.fullmatch(r"\d{5}(\.0+)?", s):
        return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(float(s)))
    return pd.to_datetime(s, errors="coerce")


def _para_json(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> registros JSON-safe (NaN/NaT viram None, datas em ISO, numpy em Python)."""
    registros = []
    for r in df.to_dict("records"):
        limpo = {}
        for k, v in r.items():
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                limpo[k] = None
            elif isinstance(v, pd.Timestamp):
                limpo[k] = v.strftime("%Y-%m-%d") if k in COLUNAS_DATA else v.strftime("%Y-%m-%dT%H:%M:%S")
            elif hasattr(v, "item"):
                limpo[k] = v.item()
            else:
                limpo[k] = v
        registros.append(limpo)
    return registros


def _enviar(supabase, tabela: str, registros: list[dict], chave: str, tentativas: int = 5) -> None:
    """Upsert em lotes com retry (conexões longas com o Supabase caem de vez em quando)."""
    for i in range(0, len(registros), LOTE):
        lote = registros[i:i + LOTE]
        for t in range(1, tentativas + 1):
            try:
                supabase.table(tabela).upsert(lote, on_conflict=chave).execute()
                break
            except Exception as e:
                if t == tentativas:
                    raise
                print(f"   ⚠️ Falha no lote ({e}); tentativa {t}/{tentativas}, aguardando...")
                time.sleep(2 * t)


def _conferir(supabase, tabela: str, chave: str, esperados: set) -> None:
    """Confere que todas as chaves enviadas estão na tabela; a raw não pode ficar incompleta."""
    achadas, inicio = set(), 0
    while True:
        dados = supabase.table(tabela).select(chave).order(chave).range(inicio, inicio + LOTE - 1).execute().data or []
        achadas.update(d[chave] for d in dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    faltando = esperados - achadas
    if faltando:
        raise RuntimeError(f"Conferência falhou: {len(faltando)} de {len(esperados)} registros ausentes em {tabela} "
                           f"(ex.: {sorted(faltando)[:5]}).")
    print(f"   ✅ Conferência: todos os {len(esperados)} registros estão em {tabela}.")


def _remover_visitas_que_sairam(supabase, tabela: str, ids_novos: set[str], limite: float) -> None:
    """A raw de visitas espelha os arquivos: visita que deixou de existir na origem sai da raw (senão contaria em dobro
    quando é editada e ganha outro id). Duas categorias: 'migradas' (mesma visita, id calculado por regra antiga: só
    troca o id) e 'sumidas' (a visita não está mais em nenhum arquivo). Aborta se as sumidas passarem de `limite`."""
    existentes, inicio = [], 0
    while True:
        dados = (supabase.table(tabela).select("id_composto,matricula,data_visita,horario_inicio,nome_consultor")
                 .order("id_composto").range(inicio, inicio + LOTE - 1).execute().data or [])
        existentes.extend(dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    obsoletas = [r for r in existentes if r["id_composto"] not in ids_novos]
    sumidas = [r for r in obsoletas
               if _id_visita(r["matricula"], str(r["data_visita"])[:10], r["horario_inicio"], r["nome_consultor"]) not in ids_novos]
    if existentes and len(sumidas) > limite * len(existentes):
        raise RuntimeError(f"{len(sumidas)} de {len(existentes)} visitas da raw ({len(sumidas) / len(existentes):.0%}) não estão em "
                           f"nenhum arquivo de visitas — acima do limite de {limite:.0%}. Confira se algum arquivo mensal foi "
                           f"movido ou apagado antes de repetir a carga (nada foi removido).")
    ids = [r["id_composto"] for r in obsoletas]
    for i in range(0, len(ids), 200):
        supabase.table(tabela).delete().in_("id_composto", ids[i:i + 200]).execute()
    print(f"   🧹 {len(ids)} linhas antigas removidas de {tabela}: {len(obsoletas) - len(sumidas)} só trocaram de id "
          f"(mesma visita) e {len(sumidas)} visitas não existem mais nos arquivos.")


def _remover_obsoletas(supabase, tabela: str, chave: str, ids_novos: set[str], limite: float = 0.2) -> None:
    """A raw de movimentação espelha a base mestra: linha que deixou de existir (ex.: evento repetido entre abas, unificado
    na carga) sai da tabela. Aborta sem remover nada se as obsoletas passarem de `limite` das linhas existentes."""
    existentes, inicio = set(), 0
    while True:
        dados = supabase.table(tabela).select(chave).order(chave).range(inicio, inicio + LOTE - 1).execute().data or []
        existentes.update(d[chave] for d in dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    obsoletas = sorted(existentes - ids_novos)
    if existentes and len(obsoletas) > limite * len(existentes):
        raise RuntimeError(f"{len(obsoletas)} de {len(existentes)} linhas de {tabela} ({len(obsoletas) / len(existentes):.0%}) "
                           f"não estão na base mestra — acima do limite de {limite:.0%}. Nada foi removido; confira o arquivo.")
    for i in range(0, len(obsoletas), 200):
        supabase.table(tabela).delete().in_(chave, obsoletas[i:i + 200]).execute()
    print(f"   🧹 {len(obsoletas)} linhas obsoletas removidas de {tabela}.")


def _ler_excel(caminho: Path, aba: str, header: int = 0) -> pd.DataFrame:
    """Lê o Excel por cópia temporária: a pasta é sincronizada e o Windows trava o arquivo aberto."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        shutil.copy2(caminho, tmp.name)
        return pd.read_excel(tmp.name, sheet_name=aba, dtype=str, header=header)
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


# ─── base mestra (ATIVOS + ROTATIVIDADE) ──────────────────────────────────────

def ler_base(caminho: Path, aba: str, aba_origem: str) -> pd.DataFrame:
    """Aba da base mestra -> linhas com matrícula, colunas padronizadas e datas convertidas."""
    bruto = _ler_excel(caminho, aba)
    bruto.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in bruto.columns]
    if "DATA.INÍCIO" not in bruto.columns:
        bruto = bruto.rename(columns={bruto.columns[0]: "DATA.INÍCIO"})   # em ROTATIVIDADE a 1ª coluna vem chamada "\"
    faltando = [c for c in MAPA_BASE if c not in bruto.columns and c != '"MOTIVO" (SAÍDA)']
    if faltando:
        raise ValueError(f"{caminho.name} [{aba}]: colunas ausentes {faltando}")
    for c in MAPA_BASE:
        if c not in bruto.columns:
            bruto[c] = None
    df = bruto[list(MAPA_BASE)].rename(columns=MAPA_BASE)
    for c in df.columns:
        df[c] = df[c].map(_limpar)
    linhas_lidas = len(df)
    df = df[df["matricula"].notna()].copy()          # as abas trazem centenas de linhas em branco no fim
    for c in ("data_inicio", "data_conclusao"):
        convertida = df[c].map(_data)
        invalidas = df.loc[convertida.isna() & df[c].notna(), c]
        if len(invalidas):
            print(f"   ⚠️ {aba}: {len(invalidas)} valores de {c} não são data e foram ignorados "
                  f"({invalidas.value_counts().head(4).to_dict()}) — o texto repete status_movimentacao.")
        df[c] = convertida
    df["latitude"] = df["latitude"].map(_numero)
    df["longitude"] = df["longitude"].map(_numero)
    fora = (df["latitude"].abs() > 90) | (df["longitude"].abs() > 180)   # ex.: -1807531 (perdeu a vírgula decimal)
    if fora.any():
        print(f"   ⚠️ {aba}: {int(fora.sum())} coordenadas fora da faixa válida (matrículas "
              f"{list(df.loc[fora, 'matricula'][:5])}); latitude/longitude ficam nulas para elas.")
        df.loc[fora, ["latitude", "longitude"]] = None
    df["aba_origem"] = aba_origem
    print(f"   -> {aba}: {len(df)} linhas com matrícula ({linhas_lidas - len(df)} em branco), "
          f"{df['matricula'].nunique()} matrículas únicas")
    return df


def _id_composto(*partes) -> str:
    return hashlib.md5("|".join(str(p if p is not None and not pd.isna(p) else "").strip() for p in partes).encode("utf-8")).hexdigest()


def _sem_acento(texto):
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return texto
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))


def _id_visita(matricula, data_iso, horario, consultor) -> str:
    """A planilha não tem número de atendimento. O consultor entra sem acento e maiúsculo: a exportação nova
    troca 'José' por 'Jose' na mesma visita, e o banco grava o nome maiúsculo — o id recalculado a partir da
    raw precisa bater com o da planilha."""
    consultor = _sem_acento(consultor)
    return _id_composto(matricula, data_iso, horario, consultor.upper() if isinstance(consultor, str) else consultor)


def montar_raw_fazendas(ativos: pd.DataFrame, rotatividade: pd.DataFrame, elabore: pd.DataFrame, prefixo: str) -> pd.DataFrame:
    """1 linha por matrícula: a de ATIVOS; senão a saída mais recente de ROTATIVIDADE."""
    saidas = rotatividade.sort_values(["data_conclusao", "data_inicio"], na_position="first") \
        .drop_duplicates("matricula", keep="last")
    base = pd.concat([ativos.assign(situacao="ATIVA"),
                      saidas[~saidas["matricula"].isin(set(ativos["matricula"]))].assign(situacao="SAIU")],
                     ignore_index=True)
    dup = base["matricula"][base["matricula"].duplicated()].unique()
    if len(dup):
        raise RuntimeError(f"Matrícula duplicada em ATIVOS (ex.: {list(dup[:5])}); a base mestra exige valor único.")

    # Elabore: 1 linha por matrícula; se houver mais de uma, vale a que tem código PRO
    elabore = elabore.assign(_pro=elabore["codigo_elabore"].fillna("").str.match(r"^PRO[0-9]{4}$")) \
        .sort_values("_pro").drop_duplicates("matricula", keep="last").drop(columns="_pro")
    df = base.merge(elabore, on="matricula", how="left")
    df["no_elabore"] = df["id_property"].notna()
    tem_codigo = df["codigo_elabore"].notna()
    df["codigo_lr"] = df["codigo_elabore"].where(tem_codigo, prefixo + df["matricula"])
    df["origem_codigo"] = tem_codigo.map({True: "ELABORE", False: "PROVISORIO"})
    repetido = df["codigo_lr"][df["codigo_lr"].duplicated()].unique()
    if len(repetido):
        raise RuntimeError(f"codigo_lr repetido entre fazendas (ex.: {list(repetido[:5])}); a raw exige código único.")
    df["data_processamento"] = _agora()
    return df[COLUNAS_FAZENDAS_RAW]


def montar_raw_movimentacao(ativos: pd.DataFrame, rotatividade: pd.DataFrame, mapa_codigo: dict, arquivo: str) -> pd.DataFrame:
    df = pd.concat([ativos, rotatividade], ignore_index=True)
    df["id_composto"] = [_id_composto(m, a, i, f, c, s) for m, a, i, f, c, s in zip(
        df["matricula"], df["aba_origem"], df["data_inicio"], df["data_conclusao"], df["consultor"], df["status_movimentacao"])]
    repetidas = int(df["id_composto"].duplicated().sum())
    if repetidas:
        print(f"   ℹ️ {repetidas} linhas idênticas repetidas na base foram unificadas.")
        df = df.drop_duplicates("id_composto")
    # O mesmo evento pode constar em ATIVOS e em ROTATIVIDADE (ex.: inclusão recente). Como aba_origem entra no id_composto,
    # as duas linhas sobreviviam e a movimentação do BI contava o evento em dobro. Vale a de ATIVOS (vem primeiro no concat).
    # O texto do status varia entre as abas ("Inclusão Do Produtor…" × "…do produtor…"), então a comparação é normalizada.
    df["_status_norm"] = df["status_movimentacao"].map(lambda s: str(_sem_acento(s) or "").strip().upper())
    chave_evento = df[["matricula", "_status_norm", "data_inicio", "data_conclusao"]].fillna("").astype(str).agg("|".join, axis=1)
    entre_abas = (df["aba_origem"] != "ATIVOS") & chave_evento.isin(set(chave_evento[df["aba_origem"] == "ATIVOS"]))
    if entre_abas.any():
        print(f"   ℹ️ {int(entre_abas.sum())} eventos repetidos entre ATIVOS e ROTATIVIDADE foram unificados (vale a linha de ATIVOS).")
        df = df[~entre_abas]
    df = df.drop(columns="_status_norm")
    df["codigo_lr"] = df["matricula"].map(mapa_codigo)
    df["arquivo_origem"] = arquivo
    df["data_processamento"] = _agora()
    return df[COLUNAS_MOVIMENTACAO_RAW]


# ─── pro_raw_visitas ──────────────────────────────────────────────────────────

def localizar_arquivos_visitas(pasta: Path, cfg_pro: dict) -> list[Path]:
    """Histórico (BD_PROCAMPO/VISITAS) + arquivo(s) do mês corrente (BD_PROCAMPO), do mais antigo ao mais novo.

    O prefixo AAAA_MM do nome dá a ordem: em caso de visita repetida entre arquivos, vale a do mais novo."""
    padrao = cfg_pro["padrao_arquivo_visitas"]
    achados = list((pasta / cfg_pro["pasta_visitas_historico"]).glob(padrao)) + list(pasta.glob(padrao))
    arquivos = sorted({a.resolve() for a in achados if not a.name.startswith("~$")}, key=lambda a: a.name)
    if not arquivos:
        raise FileNotFoundError(f"Nenhum arquivo '{padrao}' em {pasta} nem em {pasta / cfg_pro['pasta_visitas_historico']}.")
    return arquivos


def _data_visita(serie: pd.Series) -> pd.Series:
    """'21/09/2026' (formato atual) ou data ISO; o resto vira NaT."""
    data = pd.to_datetime(serie, format="%d/%m/%Y", errors="coerce")
    faltam = data.isna() & serie.notna()
    if faltam.any():
        data[faltam] = pd.to_datetime(serie[faltam], errors="coerce")
    return data


def ler_visitas_arquivo(caminho: Path, aba: str, linha_cabecalho: int) -> pd.DataFrame:
    """Um arquivo de visitas -> colunas da raw (sem código da fazenda, id ainda não deduplicado)."""
    bruto = _ler_excel(caminho, aba, header=linha_cabecalho)
    bruto.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in bruto.columns]
    faltando = [c for c in MAPA_VISITAS if c not in bruto.columns]
    if faltando:
        raise ValueError(f"{caminho.name}: colunas ausentes {faltando}")
    df = bruto[list(MAPA_VISITAS)].rename(columns=MAPA_VISITAS)
    for c in df.columns:
        df[c] = df[c].map(_limpar)
    df = df[df["matricula"].notna() | df["data_visita"].notna()].copy()   # linhas em branco no fim da exportação

    df["data_visita"] = _data_visita(df["data_visita"])
    sem_data = int(df["data_visita"].isna().sum())
    if sem_data:
        raise ValueError(f"{sem_data} linhas sem data de visita válida em {caminho.name}")
    for c in COLUNAS_NUMERICAS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in COLUNAS_BOOLEANAS:
        df[c] = df[c].map(lambda v: None if v is None else v.strip().lower() in ("sim", "s", "true", "1"))

    coords = df["localizacao"].fillna("").str.extract(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
    df["latitude"] = pd.to_numeric(coords[0], errors="coerce")
    df["longitude"] = pd.to_numeric(coords[1], errors="coerce")
    df = df.drop(columns=["localizacao"])

    # Mês de competência = AAAA_MM do nome do arquivo (2026_10_... -> 2026-10-01), mesmo que as datas sejam do mês
    # anterior (ciclo 21->20). Nome fora do padrão: cai no mês da data da visita.
    achado = re.match(r"^(\d{4})_(\d{2})_", caminho.name)
    if achado:
        df["mes_referencia"] = pd.Timestamp(int(achado.group(1)), int(achado.group(2)), 1)
    else:
        df["mes_referencia"] = df["data_visita"].dt.to_period("M").dt.to_timestamp()
    df["id_composto"] = [_id_visita(m, d.strftime("%Y-%m-%d"), h, c) for m, d, h, c in
                         zip(df["matricula"], df["data_visita"], df["horario_inicio"], df["nome_consultor"])]
    df["arquivo_origem"] = caminho.name
    return df


MAPA_VISITAS_FORA_APP = {
    "MÊS REF.": "mes_referencia",
    "CÓD. PROPRIEDADE": "matricula",
    "PRODUTOR CADASTRADO": "nome_produtor",
    "CONSULTOR": "nome_consultor",
    "EMPRESA": "empresa_responsavel",
    "DATA VISITA": "data_visita",
    "TIPO DE VISITA": "tipo_visita",
}


def localizar_arquivos_fora_app(pasta: Path, cfg_pro: dict) -> list[Path]:
    """Planilhas 'Visitas fora do app*.xlsx' (opcionais), do nome mais antigo ao mais novo."""
    padrao = cfg_pro.get("padrao_arquivo_visitas_fora_app")
    if not padrao:
        return []
    pasta_fora = pasta / cfg_pro.get("pasta_visitas_fora_app", "VISITAS_FORA_APP")
    achados = list(pasta_fora.glob(padrao)) if pasta_fora.is_dir() else []
    return sorted({a.resolve() for a in achados if not a.name.startswith("~$")}, key=lambda a: a.name)


def ler_visitas_fora_app(caminho: Path, aba) -> pd.DataFrame:
    """Planilha 'fora do app' -> colunas da raw. Sem horário e sem métricas; MÊS REF. vira mes_referencia."""
    bruto = _ler_excel(caminho, aba)
    bruto.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in bruto.columns]
    faltando = [c for c in MAPA_VISITAS_FORA_APP if c not in bruto.columns]
    if faltando:
        raise ValueError(f"{caminho.name}: colunas ausentes {faltando}")
    df = bruto[list(MAPA_VISITAS_FORA_APP)].rename(columns=MAPA_VISITAS_FORA_APP)
    for c in df.columns:
        df[c] = df[c].map(_limpar)
    df = df[df["matricula"].notna() | df["data_visita"].notna()].copy()
    df["data_visita"] = _data_visita(df["data_visita"])
    df["mes_referencia"] = _data_visita(df["mes_referencia"]).dt.to_period("M").dt.to_timestamp()
    invalidas = int(df["data_visita"].isna().sum() + df["mes_referencia"].isna().sum())
    if invalidas:
        raise ValueError(f"{caminho.name}: {invalidas} datas inválidas (DATA VISITA / MÊS REF.)")
    df["horario_inicio"] = None
    df["id_composto"] = [_id_visita(m, d.strftime("%Y-%m-%d"), None, c) for m, d, c in
                         zip(df["matricula"], df["data_visita"], df["nome_consultor"])]
    df["rascunho"] = False
    df["arquivo_origem"] = caminho.name
    return df


def montar_raw_visitas(arquivos: list[Path], aba: str, linha_cabecalho: int, mapa_codigo: dict,
                       fora_app: list[Path] | None = None, aba_fora_app=0,
                       descartadas_fora_app: list | None = None) -> pd.DataFrame:
    """Junta os arquivos (do mais antigo ao mais novo) e mantém 1 linha por visita: a do arquivo mais novo.

    As planilhas "fora do app" entram depois: visita com a mesma matrícula+data de uma visita do app é duplicata e
    é descartada (o app vale); os descartes voltam em `descartadas_fora_app`."""
    partes = []
    for a in arquivos:
        df = ler_visitas_arquivo(a, aba, linha_cabecalho)
        internas = int(df["id_composto"].duplicated().sum())
        print(f"   -> {a.name}: {len(df)} linhas, visitas de {df['data_visita'].min():%d/%m/%Y} a {df['data_visita'].max():%d/%m/%Y}"
              + (f" ({internas} repetidas dentro do arquivo)" if internas else ""))
        partes.append(df)
    todas = pd.concat(partes, ignore_index=True)
    unicas = todas.drop_duplicates("id_composto", keep="last").copy()
    print(f"   -> {len(todas)} linhas nos {len(arquivos)} arquivos; {len(todas) - len(unicas)} repetidas "
          f"(mesma visita em mais de um arquivo, vale a do mais novo) => {len(unicas)} visitas")

    if fora_app:
        chave_app = set(unicas["matricula"].astype(str) + "|" + unicas["data_visita"].dt.strftime("%Y-%m-%d"))
        fora = pd.concat([ler_visitas_fora_app(a, aba_fora_app) for a in fora_app], ignore_index=True)
        fora = fora.drop_duplicates("id_composto", keep="last")
        chave = fora["matricula"].astype(str) + "|" + fora["data_visita"].dt.strftime("%Y-%m-%d")
        repetida = chave.isin(chave_app) | chave.duplicated(keep="last")
        novas = fora[~repetida].copy()
        print(f"   -> fora do app: {len(fora)} visitas em {len(fora_app)} arquivo(s); {int(repetida.sum())} já existem "
              f"(mesma matrícula+data) e foram descartadas => {len(novas)} novas")
        if descartadas_fora_app is not None:
            descartadas_fora_app.extend(fora[repetida].assign(motivo="DUPLICADA_MESMA_MATRICULA_E_DATA").to_dict("records"))
        unicas = pd.concat([unicas, novas], ignore_index=True)

    unicas["codigo_lr"] = unicas["matricula"].map(mapa_codigo)
    unicas["data_processamento"] = _agora()
    for c in COLUNAS_VISITAS_RAW:
        if c not in unicas.columns:
            unicas[c] = None
    return unicas[COLUNAS_VISITAS_RAW].reset_index(drop=True)


# ─── visitas de matrícula fora da base: casamento por nome ────────────────────

_STOP_NOME = {"E", "DE", "DA", "DO", "DOS", "DAS", "OUTRA", "OUTRO", "OUTROS", "ESPOLIO"}


def _tokens_nome(texto) -> frozenset:
    """Nome em maiúsculas, sem acento/pontuação, sem conectivos ('E OUTRA', 'DE'...)."""
    s = _sem_acento("" if texto is None or (not isinstance(texto, str) and pd.isna(texto)) else str(texto)).upper()
    return frozenset(t for t in re.sub(r"[^A-Z0-9 ]", " ", s).split() if t not in _STOP_NOME)


def _nomes_compativeis(a: frozenset, b: frozenset) -> bool:
    """Mesmo nome, ou um contido no outro com pelo menos 2 palavras (ex.: 'MEIRE SUELI BONINI' em
    'MEIRE SUELI MARTINES BONINI')."""
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 2 and (a <= b or b <= a))


def _chave_matricula(visitas: pd.DataFrame) -> pd.Series:
    """Matrícula da visita; quando está em branco no Esteio, 'SEM_MATRICULA|produtor|fazenda' (casa por nome)."""
    sem = visitas["matricula"].isna()
    produtor = visitas["nome_produtor"].fillna("").astype(str)
    fazenda = visitas["nome_propriedade"].fillna("").astype(str)
    return visitas["matricula"].where(~sem, "SEM_MATRICULA|" + produtor + "|" + fazenda)


def casar_visitas_por_nome(visitas: pd.DataFrame, fazendas: pd.DataFrame, aliases_consultor: dict | None = None,
                           aliases_matricula: dict | None = None) -> tuple[dict, list[dict]]:
    """Resolve visitas cuja matrícula não está na base mestra (o Esteio e a base às vezes divergem na matrícula da
    mesma fazenda). Retorna ({matricula_do_arquivo: matricula_da_base}, auditoria com o critério ou o motivo).

    Ordem: alias manual (procampo.aliases_matricula) > casamento por nome. Por nome só vale com (1) produtor
    compatível E (2) fazenda igual OU consultor da visita entre os da base, e candidato único (ou único ATIVO).
    Proteção: se a fazenda candidata já recebe visita, pela própria matrícula, no mesmo mês de competência,
    a visita é de outra fazenda do mesmo produtor e não é casada."""
    aliases_consultor = {_sem_acento(k).upper(): _sem_acento(v).upper() for k, v in (aliases_consultor or {}).items()}
    na_base = set(fazendas["matricula"])
    mapa, auditoria = {}, []

    def _consultores(valor) -> list[frozenset]:
        out = []
        texto = "" if valor is None or (not isinstance(valor, str) and pd.isna(valor)) else str(valor)
        for parte in re.split(r"[/;]", texto):
            nome = _sem_acento(parte).upper().strip()
            nome = aliases_consultor.get(nome, nome)
            if nome:
                out.append(_tokens_nome(nome))
        return out

    base = []
    for r in fazendas.to_dict("records"):
        base.append({"matricula": r["matricula"], "situacao": r.get("situacao"),
                     "produtor": _tokens_nome(r.get("nome_produtor")), "fazenda": _tokens_nome(r.get("nome_propriedade")),
                     "consultores": _consultores(r.get("consultor")),
                     "rotulo": f"{r.get('nome_produtor')} / {r.get('nome_propriedade')}"})
    visitas = visitas.assign(_chave=_chave_matricula(visitas))
    com_base = visitas[visitas["_chave"].isin(na_base)]
    ja_visitada = set(zip(com_base["_chave"], pd.to_datetime(com_base["mes_referencia"])))

    sem = visitas[~visitas["_chave"].isin(na_base)]
    for matricula, g in sem.groupby("_chave"):
        linha = {"matricula_arquivo": "(em branco)" if str(matricula).startswith("SEM_MATRICULA|") else matricula,
                 "visitas": len(g), "produtor": g["nome_produtor"].iloc[0],
                 "fazenda": g["nome_propriedade"].iloc[0], "consultores": " / ".join(sorted(set(g["nome_consultor"].dropna()))),
                 "ultima_visita": g["data_visita"].max()}
        alvo = (aliases_matricula or {}).get(str(matricula))
        if alvo:
            if alvo in na_base:
                mapa[matricula] = alvo
                auditoria.append({**linha, "matricula_base": alvo, "resultado": "CASADA", "criterio": "ALIAS_MANUAL"})
            else:
                auditoria.append({**linha, "matricula_base": alvo, "resultado": "NAO_CASADA",
                                  "criterio": "ALIAS_MANUAL_PARA_MATRICULA_FORA_DA_BASE"})
            continue
        prod = _tokens_nome(linha["produtor"])
        faz = _tokens_nome(linha["fazenda"])
        cons = [c for n in g["nome_consultor"].dropna().unique() for c in _consultores(n)]
        candidatos = []
        for b in base:
            if not _nomes_compativeis(prod, b["produtor"]):
                continue
            fazenda_igual = bool(faz) and faz == b["fazenda"]
            consultor_igual = any(_nomes_compativeis(c, bc) for c in cons for bc in b["consultores"])
            if fazenda_igual or consultor_igual:
                candidatos.append((b, "PRODUTOR+FAZENDA" if fazenda_igual else "PRODUTOR+CONSULTOR"))
        if len(candidatos) > 1:
            ativos = [c for c in candidatos if c[0]["situacao"] == "ATIVA"]
            candidatos = ativos if len(ativos) == 1 else candidatos
        if not candidatos:
            auditoria.append({**linha, "matricula_base": None, "resultado": "NAO_CASADA", "criterio": "SEM_CANDIDATO"})
            continue
        if len(candidatos) > 1:
            auditoria.append({**linha, "matricula_base": ", ".join(c[0]["matricula"] for c in candidatos),
                              "resultado": "NAO_CASADA", "criterio": "AMBIGUA"})
            continue
        b, criterio = candidatos[0]
        meses = set(pd.to_datetime(g["mes_referencia"]))
        if any((b["matricula"], m) in ja_visitada for m in meses):
            auditoria.append({**linha, "matricula_base": b["matricula"], "resultado": "NAO_CASADA",
                              "criterio": "FAZENDA_CANDIDATA_JA_TEM_VISITA_NO_MES (outra fazenda do mesmo produtor?)"})
            continue
        mapa[matricula] = b["matricula"]
        auditoria.append({**linha, "matricula_base": b["matricula"], "resultado": "CASADA", "criterio": criterio,
                          "fazenda_base": b["rotulo"]})
    return mapa, auditoria


# ─── orquestração ─────────────────────────────────────────────────────────────

def executar_carga_procampo(raiz: Path | None = None) -> dict:
    import psycopg2

    raiz = raiz or raiz_projeto
    print("=" * 70)
    print("🐄 CARGA DA RAW DO PROCAMPO (pro_raw_fazendas + pro_raw_movimentacao + pro_raw_visitas)")
    print("=" * 70)
    carregar_env(raiz)
    cfg = carregar_configuracao(raiz)
    cfg_pro, tabelas = cfg["procampo"], cfg["supabase"]["tabelas"]
    pasta = Path(cfg["caminhos"]["bd_procampo"])
    prefixo = cfg_pro["prefixo_codigo_provisorio"]
    supabase = obter_cliente_supabase(raiz)

    print(f"\n📥 Lendo a base mestra {cfg_pro['arquivo_base']}...")
    ativos = ler_base(pasta / cfg_pro["arquivo_base"], cfg_pro["aba_base_ativos"], "ATIVOS")
    rotatividade = ler_base(pasta / cfg_pro["arquivo_base"], cfg_pro["aba_base_rotatividade"], "ROTATIVIDADE")

    print("\n📥 Lendo as propriedades da Piracanjuba no Elabore (analytics_mart.vw_dim_property)...")
    conexao = psycopg2.connect(
        host=os.environ["PG_HOST"], port=int(os.getenv("PG_PORT", "5432")), dbname=os.getenv("PG_DATABASE", "postgres"),
        user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], sslmode="require", connect_timeout=15,
    )
    try:
        with conexao.cursor() as cur:
            cur.execute(SQL_PROPRIEDADES_ELABORE, {"id_agro": cfg_pro["id_agroindustry"]})
            elabore = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    finally:
        conexao.close()
    print(f"   -> {len(elabore)} propriedades com matrícula no Elabore")

    fazendas = montar_raw_fazendas(ativos, rotatividade, elabore, prefixo)
    pro_fora_da_base = elabore[elabore["codigo_elabore"].fillna("").str.match(cfg_pro["padrao_codigo"])
                               & ~elabore["matricula"].isin(set(fazendas["matricula"]))]
    print(f"\n📊 {len(fazendas)} fazendas na base: {int((fazendas['situacao'] == 'ATIVA').sum())} ativas, "
          f"{int((fazendas['situacao'] == 'SAIU').sum())} saíram")
    print(f"   -> no Elabore: {int(fazendas['no_elabore'].sum())} (código do Elabore) | "
          f"fora do Elabore: {int((~fazendas['no_elabore']).sum())} (código provisório {prefixo}+matrícula, "
          f"\"Cadastro Elabore = NÃO\")")
    print(f"      ativas fora do Elabore: {int(((fazendas['situacao'] == 'ATIVA') & ~fazendas['no_elabore']).sum())}")
    if len(pro_fora_da_base):
        print(f"   ⚠️ {len(pro_fora_da_base)} fazendas PRO estão no Elabore mas não na base mestra "
              f"(matrículas: {sorted(pro_fora_da_base['matricula'])[:10]}); não entram em {tabelas['procampo_fazendas_raw']}.")
    print(f"⬆️ Enviando para {tabelas['procampo_fazendas_raw']}...")
    _enviar(supabase, tabelas["procampo_fazendas_raw"], _para_json(fazendas), "matricula")
    _conferir(supabase, tabelas["procampo_fazendas_raw"], "matricula", set(fazendas["matricula"]))

    # Só a base mestra manda: visita em matrícula fora dela (mesmo de fazenda PRO do Elabore) fica sem código
    # até a fazenda entrar na base.
    mapa_codigo = dict(zip(fazendas["matricula"], fazendas["codigo_lr"]))

    movimentacao = montar_raw_movimentacao(ativos, rotatividade, mapa_codigo, cfg_pro["arquivo_base"])
    print(f"\n📊 {len(movimentacao)} linhas de movimentação "
          f"({int((movimentacao['aba_origem'] == 'ATIVOS').sum())} de ATIVOS, {int((movimentacao['aba_origem'] == 'ROTATIVIDADE').sum())} de ROTATIVIDADE)")
    print(f"⬆️ Enviando para {tabelas['procampo_movimentacao_raw']}...")
    _enviar(supabase, tabelas["procampo_movimentacao_raw"], _para_json(movimentacao), "id_composto")
    _conferir(supabase, tabelas["procampo_movimentacao_raw"], "id_composto", set(movimentacao["id_composto"]))
    _remover_obsoletas(supabase, tabelas["procampo_movimentacao_raw"], "id_composto", set(movimentacao["id_composto"]))

    arquivos = localizar_arquivos_visitas(pasta, cfg_pro)
    print(f"\n📥 Lendo {len(arquivos)} arquivos de visitas ({arquivos[0].name} a {arquivos[-1].name})...")
    fora_app = localizar_arquivos_fora_app(pasta, cfg_pro)
    descartadas_fora_app: list[dict] = []
    visitas = montar_raw_visitas(arquivos, cfg_pro["aba_visitas"], cfg_pro["linha_cabecalho_visitas"], mapa_codigo,
                                 fora_app, cfg_pro.get("aba_visitas_fora_app", 0), descartadas_fora_app)
    # Matrícula do Esteio fora da base mestra: tenta casar por produtor + fazenda/consultor (config:
    # procampo.fallback_nome_visitas e procampo.aliases_matricula). A matrícula do arquivo é preservada na raw.
    casamentos: list[dict] = []
    if cfg_pro.get("fallback_nome_visitas", True):
        mapa_mat, casamentos = casar_visitas_por_nome(visitas, fazendas, cfg_pro.get("aliases_consultor"),
                                                       cfg_pro.get("aliases_matricula"))
        if mapa_mat:
            novo = _chave_matricula(visitas).map(mapa_mat).map(mapa_codigo)
            visitas["codigo_lr"] = visitas["codigo_lr"].fillna(novo)
        for c in casamentos:
            marca = "✅ casada" if c["resultado"] == "CASADA" else "⚠️ sem casamento"
            destino = f" -> {c['matricula_base']} ({c.get('fazenda_base', '')})" if c["resultado"] == "CASADA" else ""
            print(f"   {marca}: {c['matricula_arquivo']} {c['produtor']} / {c['fazenda']} ({c['visitas']} visitas){destino}"
                  f" [{c['criterio']}]")
    sem_fazenda = visitas[visitas["codigo_lr"].isna()]
    codigo = visitas["codigo_lr"].fillna("")
    print(f"   -> {len(visitas)} visitas ({visitas['data_visita'].min():%Y-%m-%d} a {visitas['data_visita'].max():%Y-%m-%d}); "
          f"rascunhos: {int(visitas['rascunho'].fillna(False).sum())}")
    print(f"   -> em fazenda PRO: {int(codigo.str.match(cfg_pro['padrao_codigo']).sum())} | "
          f"em fazenda LR (SmartQuestion): {int(codigo.str.startswith('LR').sum())} | "
          f"em fazenda fora do Elabore (código {prefixo}...): {int(codigo.str.startswith(prefixo).sum())}")
    print(f"   -> sem fazenda na base ({sem_fazenda['matricula'].nunique()} matrículas, ex.: "
          f"{sorted(sem_fazenda['matricula'].dropna().unique())[:5]}): {len(sem_fazenda)} visitas ficam na raw sem código")
    fora_sem_fazenda = sem_fazenda[sem_fazenda["arquivo_origem"].isin({a.name for a in fora_app})]
    if casamentos:
        saida = raiz / "data" / "output"
        saida.mkdir(parents=True, exist_ok=True)
        caminho = saida / f"{datetime.now():%Y_%m_%d_%H%M%S}_procampo_visitas_matricula_fora_da_base.xlsx"
        pd.DataFrame(casamentos).to_excel(caminho, index=False)
        print(f"   -> matrículas fora da base: {sum(c['resultado'] == 'CASADA' for c in casamentos)} casadas por nome, "
              f"{sum(c['resultado'] != 'CASADA' for c in casamentos)} sem casamento: {caminho.name}")
    if descartadas_fora_app or len(fora_sem_fazenda):
        saida = raiz / "data" / "output"
        saida.mkdir(parents=True, exist_ok=True)
        caminho = saida / f"{datetime.now():%Y_%m_%d_%H%M%S}_procampo_visitas_fora_do_app_nao_carregadas.xlsx"
        with pd.ExcelWriter(caminho) as xl:
            pd.DataFrame(descartadas_fora_app).to_excel(xl, sheet_name="duplicadas_do_app", index=False)
            fora_sem_fazenda.to_excel(xl, sheet_name="sem_fazenda_na_base", index=False)
        print(f"   -> fora do app: {len(descartadas_fora_app)} duplicadas e {len(fora_sem_fazenda)} sem fazenda na base "
              f"(ficam na raw sem código): {caminho.name}")
    print(f"⬆️ Enviando para {tabelas['procampo_visitas_raw']}...")
    _enviar(supabase, tabelas["procampo_visitas_raw"], _para_json(visitas), "id_composto")
    _conferir(supabase, tabelas["procampo_visitas_raw"], "id_composto", set(visitas["id_composto"]))
    _remover_visitas_que_sairam(supabase, tabelas["procampo_visitas_raw"], set(visitas["id_composto"]),
                                float(cfg_pro.get("limite_remocao_visitas_pct", 0.10)))
    print("=" * 70)
    return {"fazendas": fazendas, "movimentacao": movimentacao, "visitas": visitas}


if __name__ == "__main__":
    executar_carga_procampo()
