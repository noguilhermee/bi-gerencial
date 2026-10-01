# -*- coding: utf-8 -*-
"""
Script: publicar_dim_procampo.py
Objetivo: Mesclar as fazendas do Procampo (pro_raw_fazendas) em tab_dim_fazendas_ativas, uma linha por
          fazenda × mês, ao lado das fazendas do SmartQuestion.

Rode DEPOIS de reconciliar_movimentacao_e_ativos.py: a reconciliação apaga e reconstrói cada mês da
tab_dim_fazendas_ativas só com as fontes do SmartQuestion, então as linhas do Procampo precisam ser
republicadas a cada execução. As linhas do Procampo são identificadas por
unidade_atendimento = procampo.unidade_atendimento_bi ("PROCAMPO").

Quem entra (fazendas PRO#### e provisórias MAT...; fazenda LR###### da base do Procampo também entra, mas só
nos meses em que o SmartQuestion não a lista, para não duplicar a linha — a agroindústria Piracanjuba de todo o
histórico dela é resolvida em camada_consumo.py):
  * mês da DATA.INÍCIO da base até o mês anterior ao da DATA.CONCLUSÃO (fazenda ATIVA não tem fim);
  * fazenda que SAIU sem data de conclusão: só nos meses em que houve visita;
  * todo mês com visita (não rascunho) conta, como no SmartQuestion ("visita torna a atividade incontestável").
Os meses são os que já existem na tab_dim_fazendas_ativas (o mesmo intervalo da reconciliação).
Idempotente: upsert por id + remoção das linhas do Procampo que deixaram de existir (ex.: fazenda que
trocou o código provisório MAT pelo PRO real quando o consultor a cadastrou no Elabore).

Uso:
  python scripts/functions/publicar_dim_procampo.py
"""
from __future__ import annotations

import sys
import time
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
    if (candidato / "scripts").is_dir() and ((candidato / "db").is_dir() or (candidato / "dashboard").is_dir()):
        raiz_projeto = candidato
        break
else:
    raiz_projeto = caminho_atual.parents[2]
for p in [raiz_projeto, raiz_projeto / "scripts", raiz_projeto / "scripts" / "functions"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from functions.function import carregar_env, obter_cliente_supabase  # noqa: E402
from functions import regras_negocio as rn  # noqa: E402

LOTE = 1000
TABELA_DIM = "tab_dim_fazendas_ativas"


def _buscar_tudo(supabase, tabela: str, colunas: str, ordem: str, filtro: tuple | None = None) -> pd.DataFrame:
    registros, inicio = [], 0
    while True:
        q = supabase.table(tabela).select(colunas)
        if filtro:
            q = q.eq(*filtro)
        dados = q.order(ordem).range(inicio, inicio + LOTE - 1).execute().data or []
        registros.extend(dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    return pd.DataFrame(registros)


def _meses_da_dim(supabase) -> list[pd.Timestamp]:
    """Intervalo de meses já existente na dim (a reconciliação define a janela)."""
    menor = supabase.table(TABELA_DIM).select("mes_referencia").order("mes_referencia").limit(1).execute().data
    maior = supabase.table(TABELA_DIM).select("mes_referencia").order("mes_referencia", desc=True).limit(1).execute().data
    if not menor or not maior:
        raise RuntimeError(f"{TABELA_DIM} está vazia: rode a reconciliação (reconciliar_movimentacao_e_ativos.py) antes.")
    return list(pd.date_range(pd.Timestamp(menor[0]["mes_referencia"]), pd.Timestamp(maior[0]["mes_referencia"]), freq="MS"))


def _mes(valor) -> pd.Timestamp | None:
    d = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(d) else d.to_period("M").to_timestamp()


def _texto(valor, padrao=None):
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return padrao
    s = str(valor).strip()
    return s if s and s.lower() not in ("nan", "none", "nat") else padrao


def montar_linhas(fazendas: pd.DataFrame, visitas: pd.DataFrame, meses: list[pd.Timestamp], cfg_pro: dict,
                  meses_smartquestion: set[tuple] | None = None) -> pd.DataFrame:
    regioes = cfg_pro.get("regioes_por_sigla", {})
    aliases = cfg_pro.get("aliases_consultor", {})
    marcador = cfg_pro["unidade_atendimento_bi"]
    agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    meses_smartquestion = meses_smartquestion or set()
    visitadas: set[tuple] = set()
    if not visitas.empty:
        v = visitas[visitas["rascunho"].fillna(False).astype(bool) == False]  # noqa: E712
        v = v[v["codigo_lr"].notna()]
        visitadas = set(zip(v["codigo_lr"], pd.to_datetime(v["data_visita"], errors="coerce").dt.to_period("M").dt.to_timestamp()))

    # A planilha de visitas começa em fev/2026: antes disso toda fazenda apareceria como "Nunca visitado".
    # A carteira do Procampo começa no primeiro mês com visita.
    if visitadas:
        primeiro = min(m for _, m in visitadas if pd.notna(m))
        meses = [m for m in meses if m >= primeiro]

    linhas = []
    for r in fazendas.to_dict("records"):
        cod = r["codigo_lr"]
        ini, fim = _mes(r.get("data_inicio")), _mes(r.get("data_conclusao"))
        saiu = r.get("situacao") == "SAIU"
        for m in meses:
            if saiu:
                na_janela = ini is not None and fim is not None and ini <= m < fim
            else:
                na_janela = ini is not None and ini <= m
            if not (na_janela or (cod, m) in visitadas):
                continue
            if (cod, m) in meses_smartquestion:
                continue   # fazenda LR: nos meses em que o SmartQuestion a lista, a linha dele já existe
            coord = rn.sanear_coordenada(r.get("latitude"), r.get("longitude"), rn.sigla_uf(r.get("uf")))
            # sem acento e em maiúsculas, como os consultores do SmartQuestion no BI (a exportação de visitas
            # às vezes troca 'José' por 'Jose'; com grafias diferentes o mesmo consultor apareceria duas vezes)
            consultor = rn.consultor_procampo(r.get("consultor"), aliases) or None
            linhas.append({
                "id": f"{cod}_{m:%Y_%m_%d}",
                "codigo_produtor": cod,
                "nome_produtor": _texto(r.get("nome_produtor"), "PRODUTOR"),
                "estado": _texto(r.get("uf"), "NÃO INFORMADO"),
                "cidade": _texto(r.get("cidade"), "NÃO INFORMADA"),
                "nome_propriedade": _texto(r.get("nome_propriedade")),
                "tipo_ponto_atendimento": "LEITE",
                "unidade_atendimento": marcador,
                # no Procampo o grupo é o próprio consultor (não há o formato "NOME (PROJETO) / ..." do SmartQuestion)
                "grupo_ponto_atendimento": consultor,
                "consultor": consultor,
                "projeto": cfg_pro["projeto_bi"],
                "agroindustria": cfg_pro["agroindustria_bi"],
                # sigla sem correspondência (ex.: NSG) -> "NÃO INFORMADA"; sem isso a camada de consumo cairia
                # no nome da unidade de atendimento ("Procampo") como região
                "regiao": regioes.get(_texto(r.get("regional"), "").upper(), "NÃO INFORMADA"),
                # coordenada da base do Procampo (LOCAL.LATITUDE/LONGITUDE), saneada como as demais
                "latitude": coord[0] if coord else None,
                "longitude": coord[1] if coord else None,
                "origem_coordenada": "PROCAMPO" if coord else None,
                "coordenada_corrigida": coord[2] if coord else None,
                "codigo_agroindustria": _texto(r.get("matricula")),
                "codigo_fazenda": None,
                "status": "Ativo",
                "mes_referencia": m.strftime("%Y-%m-%d"),
                "data_processamento": agora,
            })
    return pd.DataFrame(linhas)


def _enviar(supabase, registros: list[dict], tentativas: int = 5) -> None:
    for i in range(0, len(registros), LOTE):
        lote = registros[i:i + LOTE]
        for t in range(1, tentativas + 1):
            try:
                supabase.table(TABELA_DIM).upsert(lote, on_conflict="id").execute()
                break
            except Exception as e:
                if t == tentativas:
                    raise
                print(f"   ⚠️ Falha no lote ({e}); tentativa {t}/{tentativas}, aguardando...")
                time.sleep(2 * t)


def executar_publicacao_dim_procampo(raiz: Path | None = None) -> pd.DataFrame:
    raiz = raiz or raiz_projeto
    print("=" * 70)
    print(f"🐄 PUBLICANDO FAZENDAS DO PROCAMPO EM {TABELA_DIM}")
    print("=" * 70)
    carregar_env(raiz)
    with open(raiz / "scripts" / "config" / "config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg_pro, tabelas = cfg["procampo"], cfg["supabase"]["tabelas"]
    supabase = obter_cliente_supabase(raiz)

    meses = _meses_da_dim(supabase)
    fazendas = _buscar_tudo(supabase, tabelas["procampo_fazendas_raw"],
                            "matricula,codigo_lr,situacao,nome_propriedade,nome_produtor,uf,cidade,regional,consultor,"
                            "data_inicio,data_conclusao,latitude,longitude", "matricula")
    if fazendas.empty:
        raise RuntimeError(f"{tabelas['procampo_fazendas_raw']} está vazia: rode antes scripts/functions/carregar_procampo.py.")
    visitas = _buscar_tudo(supabase, tabelas["procampo_visitas_raw"], "id_composto,codigo_lr,data_visita,rascunho", "id_composto")
    print(f"   -> {len(fazendas)} fazendas na raw, {len(visitas)} visitas, meses {meses[0]:%Y-%m} a {meses[-1]:%Y-%m}")

    # (código, mês) que o SmartQuestion já lista na dim: não duplicar a fazenda LR do Procampo nesses meses
    marcador = cfg_pro["unidade_atendimento_bi"]
    codigos_lr = [c for c in fazendas["codigo_lr"].dropna().unique() if rn.eh_fazenda_smartquestion(c)]
    meses_sq: set[tuple] = set()
    for i in range(0, len(codigos_lr), 100):
        dados = supabase.table(TABELA_DIM).select("codigo_produtor,mes_referencia,unidade_atendimento")             .in_("codigo_produtor", codigos_lr[i:i + 100]).limit(5000).execute().data or []
        meses_sq.update((d["codigo_produtor"], _mes(d["mes_referencia"])) for d in dados if d.get("unidade_atendimento") != marcador)
    print(f"   -> {len(codigos_lr)} fazendas LR na base do Procampo; {len(meses_sq)} (fazenda, mês) já listados pelo SmartQuestion")

    linhas = montar_linhas(fazendas, visitas, meses, cfg_pro, meses_sq)
    if linhas.empty:
        raise RuntimeError("Nenhuma fazenda do Procampo caiu nos meses da dim; confira as datas de início na raw.")
    por_mes = linhas.groupby("mes_referencia").size()
    print("   -> fazendas por mês:", {k[:7]: int(v) for k, v in por_mes.items()})
    print(f"   -> {linhas['codigo_produtor'].nunique()} fazendas distintas "
          f"({int(linhas['codigo_produtor'].str.startswith('PRO').sum())} linhas PRO, "
          f"{int(linhas['codigo_produtor'].str.startswith(cfg_pro['prefixo_codigo_provisorio']).sum())} linhas provisórias)")
    print(f"   -> sem região: {int(linhas['regiao'].isna().sum())} linhas")

    campos_coord = ["latitude", "longitude", "origem_coordenada", "coordenada_corrigida"]
    try:
        supabase.table(TABELA_DIM).select(",".join(campos_coord)).limit(1).execute()
    except Exception:
        print(f"   ⚠️ {TABELA_DIM} ainda não tem as colunas de coordenada (rode db/input/2026_09_30_ddl_coordenadas_dim.sql); "
              "as coordenadas do Procampo não serão gravadas.")
        linhas = linhas.drop(columns=campos_coord)
    else:
        print(f"   -> coordenada válida em {int(linhas['latitude'].notna().sum())} de {len(linhas)} linhas do Procampo.")
    registros = linhas.astype(object).where(linhas.notna(), None).to_dict("records")
    print(f"⬆️ Enviando {len(registros)} linhas para {TABELA_DIM}...")
    _enviar(supabase, registros)

    existentes = set(_buscar_tudo(supabase, TABELA_DIM, "id", "id", ("unidade_atendimento", marcador)).get("id", pd.Series(dtype=str)))
    obsoletas = sorted(existentes - set(linhas["id"]))
    for i in range(0, len(obsoletas), 200):
        supabase.table(TABELA_DIM).delete().in_("id", obsoletas[i:i + 200]).execute()
    print(f"   ✅ {len(registros)} linhas publicadas, {len(obsoletas)} obsoletas do Procampo removidas.")
    print("=" * 70)
    return linhas


if __name__ == "__main__":
    executar_publicacao_dim_procampo()
