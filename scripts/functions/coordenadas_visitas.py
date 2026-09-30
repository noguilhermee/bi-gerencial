# -*- coding: utf-8 -*-
"""
Coordenada das fazendas do SmartQuestion, extraída das planilhas de visita.

LISTA_GERAL_VISITAS.xlsx (e os backups em BACKUPS/VISITAS/LISTA_GERAL_VISITAS_*.xlsx) trazem, em cada visita, a
"Latitude/Longitude (Produtor)" (cadastro do produtor no SmartQuestion) e a "(Visita)" (onde o consultor estava).
Só a do produtor é usada: é a mesma em todas as visitas da fazenda e, nas checagens contra o Elabore, foi a que caiu
no município certo quando as duas divergiam.

Cada coordenada passa por regras_negocio.sanear_coordenada (troca lat/long, sinal perdido, zero, fora da UF) e a
fazenda fica com a mediana das coordenadas válidas de todas as suas visitas.

Usado por reconciliar_movimentacao_e_ativos.py, que grava o resultado em sq_raw_fazendas_grupo e em
tab_dim_fazendas_ativas (latitude, longitude, origem_coordenada).
"""
from __future__ import annotations

import sys
from pathlib import Path

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
except ImportError:  # pragma: no cover
    import regras_negocio as rn

ORIGEM_VISITAS = "SMARTQUESTION_VISITAS"

# nomes das colunas nas planilhas, já normalizados por rn.chave (sem acento, maiúsculo)
COL_CODIGO = "CODIGO DO(A) PRODUTOR(A)"
COL_LATITUDE = "LATITUDE (PRODUTOR)"
COL_LONGITUDE = "LONGITUDE (PRODUTOR)"
COL_ESTADO = "ESTADO"


def arquivos_de_visita(bd_smartquestion: Path) -> list[Path]:
    """LISTA_GERAL_VISITAS.xlsx (mês corrente) + backups históricos, se existirem."""
    arquivos = []
    atual = bd_smartquestion / "LISTA_GERAL_VISITAS.xlsx"
    if atual.is_file():
        arquivos.append(atual)
    pasta = bd_smartquestion / "BACKUPS" / "VISITAS"
    if pasta.is_dir():
        arquivos.extend(sorted(pasta.glob("LISTA_GERAL_VISITAS_*.xlsx")))
    return arquivos


def carregar_coordenadas_smartquestion(bd_smartquestion: Path | str, verbose: bool = True) -> dict[str, dict]:
    """{codigo_lr: {latitude, longitude, origem_coordenada, coordenada_corrigida, visitas}} das fazendas do SmartQuestion.

    Arquivo ilegível ou sem as colunas esperadas é ignorado com aviso; devolve {} se nenhum servir."""
    bd = Path(bd_smartquestion)
    pontos: dict[str, list[tuple[float, float, bool]]] = {}
    lidos = 0
    for arquivo in arquivos_de_visita(bd):
        try:
            df = pd.read_excel(arquivo, sheet_name="BD")
        except Exception as e:  # arquivo aberto no Excel, corrompido, sem a aba BD...
            if verbose:
                print(f"   ⚠️ Coordenadas: {arquivo.name} não pôde ser lido ({e}).")
            continue
        colunas = {rn.chave(c): c for c in df.columns}
        faltam = [c for c in (COL_CODIGO, COL_LATITUDE, COL_LONGITUDE) if c not in colunas]
        if faltam:
            if verbose:
                print(f"   ⚠️ Coordenadas: {arquivo.name} sem as colunas {faltam}; ignorado.")
            continue
        lidos += 1
        estados = df[colunas[COL_ESTADO]] if COL_ESTADO in colunas else pd.Series([None] * len(df))
        for cod, uf, lat, lon in zip(df[colunas[COL_CODIGO]], estados, df[colunas[COL_LATITUDE]], df[colunas[COL_LONGITUDE]]):
            codigo = rn.limpar_codigo_lr(cod)
            if not codigo:
                continue
            s = rn.sanear_coordenada(lat, lon, uf)
            if s:
                pontos.setdefault(codigo, []).append(s)

    resultado: dict[str, dict] = {}
    for codigo, lista in pontos.items():
        serie = pd.DataFrame(lista, columns=["lat", "lon", "corrigida"])
        resultado[codigo] = {
            "latitude": round(float(serie["lat"].median()), 6),
            "longitude": round(float(serie["lon"].median()), 6),
            "origem_coordenada": ORIGEM_VISITAS,
            "coordenada_corrigida": bool(serie["corrigida"].mean() > 0.5),
            "visitas": len(lista),
        }
    if verbose:
        print(f"   -> Coordenadas do SmartQuestion: {len(resultado)} fazendas com coordenada válida em {lidos} planilha(s) de visita.")
    return resultado
