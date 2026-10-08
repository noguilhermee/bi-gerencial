# -*- coding: utf-8 -*-
"""
Script: carregar_status_consultor.py
Objetivo: Ler BD_STATUS_USUARIO_SQ.xlsx (exportação do histórico de usuários do SmartQuestion) num único lugar.

A planilha é o HISTÓRICO de alterações do cadastro: 1 linha por alteração (historyType CREATED/UPDATED), com o
estado completo do usuário depois dela. A chave do usuário é `originalOid`: o nome muda ao longo do tempo
(ex.: "GUILHERME FONTES (BIOMA)" -> "GUILHERME FONTES") e há homônimos com contas diferentes, então nada aqui
agrupa por nome.

  sq_raw_inativacoes_consultor <- 1 linha por inativação (Ativo passa de Sim para Não), com o nome do usuário na
                                  data da inativação. Espelha a planilha: upsert + remoção do que saiu dela.
  consultores_inativos()        -> nomes (chave sem acento, maiúscula) cujo usuário está inativo HOJE; usado por
                                  reconciliar_movimentacao_e_ativos.py para tirar da carteira os grupos sem
                                  consultor ativo.

Uso:
  python scripts/functions/carregar_status_consultor.py
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
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
    if (candidato / "scripts").is_dir() and ((candidato / "data").is_dir() or (candidato / "app").is_dir()):
        raiz_projeto = candidato
        break
else:
    raiz_projeto = caminho_atual.parents[2]
for p in [raiz_projeto, raiz_projeto / "scripts", raiz_projeto / "scripts" / "functions"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from functions.function import carregar_env, obter_cliente_supabase  # noqa: E402
from functions.regras_negocio import chave  # noqa: E402

LOTE = 1000
LIMITE_REMOCAO_PCT = 0.10   # aborta a limpeza se mais que isso das inativações da raw sumir da planilha

# Colunas da exportação -> nomes internos
MAPA_COLUNAS = {
    "originalOid": "id_usuario",
    "historyCreationDate": "data_alteracao",
    "historyType": "tipo_alteracao",
    "Nome": "nome",
    "Login": "login",
    "Ativo": "ativo",
}
ORDEM_TIPO = {"CREATED": 0, "UPDATED": 1}   # CREATED e o 1º UPDATED podem ter o mesmo instante


def carregar_configuracao(raiz: Path) -> dict:
    with open(raiz / "scripts" / "config" / "config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def caminho_planilha(raiz: Path | None = None) -> Path:
    cfg = carregar_configuracao(raiz or raiz_projeto)
    return Path(cfg["caminhos"]["bd_smartquestion"]) / cfg["smartquestion"]["arquivos"]["inativacao_consultores"]


def _ler_excel(caminho: Path, **kwargs) -> pd.DataFrame:
    """Lê o Excel por cópia temporária: a pasta é sincronizada e o Windows trava o arquivo aberto."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        shutil.copy2(caminho, tmp.name)
        return pd.read_excel(tmp.name, dtype=str, **kwargs)
    finally:
        try:
            os.remove(tmp.name)
        except Exception:
            pass


def ler_historico_status(caminho: Path | None = None) -> pd.DataFrame:
    """Planilha -> 1 linha por alteração, ordenada por usuário e momento da alteração.

    O cabeçalho é a linha que contém 'historyCreationDate' (hoje a 4ª: antes vêm o título "Usuário" e linhas em branco)."""
    caminho = caminho or caminho_planilha()
    bruto = _ler_excel(caminho, header=None)
    linha_cabecalho = next((i for i in range(min(15, len(bruto)))
                            if "historyCreationDate" in [str(v).strip() for v in bruto.iloc[i].tolist()]), None)
    if linha_cabecalho is None:
        raise ValueError(f"{caminho.name}: cabeçalho com 'historyCreationDate' não encontrado nas 15 primeiras linhas.")
    df = bruto.iloc[linha_cabecalho + 1:].copy()
    df.columns = [str(c).strip() for c in bruto.iloc[linha_cabecalho].tolist()]
    faltando = [c for c in MAPA_COLUNAS if c not in df.columns]
    if faltando:
        raise ValueError(f"{caminho.name}: colunas ausentes {faltando}")

    df = df[list(MAPA_COLUNAS)].rename(columns=MAPA_COLUNAS)
    for c in ("id_usuario", "tipo_alteracao", "nome", "login", "ativo"):
        df[c] = df[c].where(df[c].notna(), None).map(lambda v: str(v).strip() if v is not None else None)
    df = df[df["id_usuario"].notna()].copy()
    df["id_usuario"] = df["id_usuario"].str.replace(r"\.0$", "", regex=True)
    # ISO8601: a maioria vem com milissegundos ("2026-09-16 11:53:37.679000"), algumas sem ("2024-12-02 14:39:30")
    df["data_alteracao"] = pd.to_datetime(df["data_alteracao"], format="ISO8601", errors="coerce")
    sem_data = int(df["data_alteracao"].isna().sum())
    if sem_data:
        raise ValueError(f"{caminho.name}: {sem_data} linhas sem historyCreationDate válida")
    df["ativo"] = df["ativo"].map(lambda v: chave(v) in ("SIM", "S", "TRUE", "1"))
    df["_ordem_tipo"] = df["tipo_alteracao"].map(lambda v: ORDEM_TIPO.get(chave(v), 2))
    return (df.sort_values(["id_usuario", "data_alteracao", "_ordem_tipo"], kind="stable")
              .drop(columns="_ordem_tipo").reset_index(drop=True))


def montar_inativacoes(historico: pd.DataFrame) -> pd.DataFrame:
    """Cada passagem de Ativo Sim -> Não de um mesmo usuário é uma inativação (reativações abrem espaço para outra).

    O nome gravado é o da última alteração ainda ativa: na inativação o cadastro costuma ser renomeado para
    "EXCLUIR", "DUPLICATA", "INATIVO"..., e os grupos de atendimento guardam o nome de quando ele atendia."""
    df = historico.copy()
    df["ativo_anterior"] = df.groupby("id_usuario")["ativo"].shift(1)
    df["nome_anterior"] = df.groupby("id_usuario")["nome"].shift(1)
    inat = df[(df["ativo_anterior"] == True) & (~df["ativo"])].copy()  # noqa: E712 (shift gera object com NaN)
    inat["nome"] = inat["nome_anterior"].fillna(inat["nome"])
    inat["data_inativacao"] = inat["data_alteracao"].dt.strftime("%Y-%m-%d")
    inat["id_inativacao_consultor"] = [hashlib.md5(f"{u}_{d}".encode()).hexdigest()
                                       for u, d in zip(inat["id_usuario"], inat["data_inativacao"])]
    # Duas inativações do mesmo usuário no mesmo dia (inativou, reativou, inativou): vale a última do dia
    inat = inat.drop_duplicates("id_inativacao_consultor", keep="last")
    inat = inat.rename(columns={"nome": "nome_consultor"})
    inat["data_modificacao"] = datetime.now(timezone.utc).isoformat()
    return inat[["id_inativacao_consultor", "nome_consultor", "data_inativacao", "data_modificacao"]].reset_index(drop=True)


def status_atual_por_usuario(historico: pd.DataFrame) -> pd.DataFrame:
    """1 linha por usuário com o estado da última alteração e todos os nomes (chave) que ele já usou."""
    ultimo = historico.groupby("id_usuario", sort=False).tail(1).set_index("id_usuario")
    nomes = historico.assign(_chave=historico["nome"].map(chave)).groupby("id_usuario")["_chave"] \
                     .agg(lambda s: sorted({n for n in s if n}))
    return pd.DataFrame({"nome_atual": ultimo["nome"], "ativo": ultimo["ativo"],
                         "ultima_alteracao": ultimo["data_alteracao"], "nomes": nomes}).reset_index()


def consultores_inativos(historico: pd.DataFrame | None = None) -> set[str]:
    """Nomes (chave) inativos hoje. Inclui os nomes antigos do usuário, porque os grupos de atendimento do
    SmartQuestion guardam o nome da época. Um nome usado por mais de uma conta só é inativo se TODAS estiverem
    inativas (ex.: conta antiga desativada e conta nova ativa com o mesmo nome)."""
    status = status_atual_por_usuario(historico if historico is not None else ler_historico_status())
    por_nome = status.explode("nomes").dropna(subset=["nomes"]).groupby("nomes")["ativo"].any()
    return set(por_nome[~por_nome].index)


# ─── carga de sq_raw_inativacoes_consultor ────────────────────────────────────

def _enviar(supabase, tabela: str, registros: list[dict], tentativas: int = 5) -> None:
    for i in range(0, len(registros), LOTE):
        lote = registros[i:i + LOTE]
        for t in range(1, tentativas + 1):
            try:
                supabase.table(tabela).upsert(lote, on_conflict="id_inativacao_consultor").execute()
                break
            except Exception as e:
                if t == tentativas:
                    raise
                print(f"   ⚠️ Falha no lote ({e}); tentativa {t}/{tentativas}, aguardando...")
                time.sleep(2 * t)


def _remover_que_sairam(supabase, tabela: str, novas: pd.DataFrame) -> None:
    """A raw espelha a planilha. 'Migradas' = mesma inativação (nome + data) com id antigo, de quando o id era
    calculado pelo nome; 'sumidas' = não estão mais na planilha. Aborta se as sumidas passarem do limite."""
    existentes, inicio = [], 0
    while True:
        dados = (supabase.table(tabela).select("id_inativacao_consultor,nome_consultor,data_inativacao")
                 .order("id_inativacao_consultor").range(inicio, inicio + LOTE - 1).execute().data or [])
        existentes.extend(dados)
        if len(dados) < LOTE:
            break
        inicio += LOTE
    ids_novos = set(novas["id_inativacao_consultor"])
    pares_novos = {(chave(n), str(d)[:10]) for n, d in zip(novas["nome_consultor"], novas["data_inativacao"])}
    obsoletas = [r for r in existentes if r["id_inativacao_consultor"] not in ids_novos]
    sumidas = [r for r in obsoletas if (chave(r["nome_consultor"]), str(r["data_inativacao"])[:10]) not in pares_novos]
    if existentes and len(sumidas) > LIMITE_REMOCAO_PCT * len(existentes):
        raise RuntimeError(f"{len(sumidas)} de {len(existentes)} inativações da raw ({len(sumidas) / len(existentes):.0%}) "
                           f"não estão na planilha — acima do limite de {LIMITE_REMOCAO_PCT:.0%}. Confira se a exportação "
                           f"veio completa antes de repetir a carga (nada foi removido).")
    ids = [r["id_inativacao_consultor"] for r in obsoletas]
    for i in range(0, len(ids), 200):
        supabase.table(tabela).delete().in_("id_inativacao_consultor", ids[i:i + 200]).execute()
    print(f"   🧹 {len(ids)} linhas antigas removidas de {tabela}: {len(obsoletas) - len(sumidas)} só trocaram de id "
          f"(mesma inativação) e {len(sumidas)} não existem mais na planilha"
          + (f" (ex.: {[(r['nome_consultor'], r['data_inativacao']) for r in sumidas[:5]]})." if sumidas else "."))


def executar_carga_status_consultor(raiz: Path | None = None) -> pd.DataFrame:
    raiz = raiz or raiz_projeto
    print("=" * 70)
    print("👤 CARGA DAS INATIVAÇÕES DE CONSULTOR (BD_STATUS_USUARIO_SQ.xlsx -> sq_raw_inativacoes_consultor)")
    print("=" * 70)
    carregar_env(raiz)
    cfg = carregar_configuracao(raiz)
    tabela = cfg["supabase"]["tabelas"]["inativacao_consultores_staging"]
    caminho = caminho_planilha(raiz)

    print(f"\n📥 Lendo {caminho.name}...")
    historico = ler_historico_status(caminho)
    status = status_atual_por_usuario(historico)
    print(f"   -> {len(historico)} alterações de {len(status)} usuários "
          f"({historico['data_alteracao'].min():%d/%m/%Y} a {historico['data_alteracao'].max():%d/%m/%Y})")
    print(f"   -> hoje: {int(status['ativo'].sum())} usuários ativos, {int((~status['ativo']).sum())} inativos; "
          f"{int((status['nomes'].map(len) > 1).sum())} usuários mudaram de nome")

    inativacoes = montar_inativacoes(historico)
    print(f"   -> {len(inativacoes)} inativações (Ativo Sim -> Não)")

    supabase = obter_cliente_supabase(raiz)
    print(f"⬆️ Enviando para {tabela}...")
    _enviar(supabase, tabela, inativacoes.to_dict(orient="records"))
    _remover_que_sairam(supabase, tabela, inativacoes)
    print("=" * 70)
    return inativacoes


if __name__ == "__main__":
    executar_carga_status_consultor()
