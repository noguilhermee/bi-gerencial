# -*- coding: utf-8 -*-
"""
Leitura única do scripts/config/config.yaml, com os caminhos ajustados à máquina que está rodando.

O config.yaml traz os caminhos do notebook (C:/Users/Guilherme/...). Em outra máquina (o VPS da empresa), cada
caminho pode ser trocado por uma variável no scripts/config/.env, sem mexer no config.yaml versionado:

    LR_CAMINHO_<CHAVE>        substitui caminhos.<chave>     (ex.: LR_CAMINHO_BD_SMARTQUESTION=/srv/lr-pipeline/BD_SMARTQUESTION)
    LR_ELABORE_PROJETO        substitui indicadores_elabore.projeto

Caminho relativo (no config.yaml ou no .env) vale a partir da raiz do projeto (bi-gerencial/), não da pasta de onde o
script foi chamado.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml


def _arquivo_config(raiz: Path) -> Path:
    arquivo = raiz / "scripts" / "config" / "config.yaml"
    if not arquivo.is_file():
        arquivo = raiz / "SCRIPTS" / "CONFIG" / "config.yaml"
    if not arquivo.is_file():
        raise FileNotFoundError(f"❌ Arquivo de configuração não encontrado: {arquivo}")
    return arquivo


def _carregar_env(raiz: Path) -> None:
    """Variáveis do .env do projeto, sem sobrescrever as que já vieram do ambiente (cron, systemd)."""
    env = raiz / "scripts" / "config" / ".env"
    if not env.is_file():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env, override=False)
    except ImportError:
        pass


def _resolver(valor: str, raiz: Path) -> str:
    caminho = Path(os.path.expanduser(str(valor)))
    return str(caminho if caminho.is_absolute() else (raiz / caminho).resolve())


def ajustar_config(cfg: dict, raiz: Path | str) -> dict:
    """Aplica as variáveis LR_CAMINHO_* / LR_ELABORE_PROJETO e resolve caminhos relativos a partir da raiz."""
    raiz = Path(raiz).resolve()
    _carregar_env(raiz)

    caminhos = cfg.get("caminhos") or {}
    for chave, valor in list(caminhos.items()):
        valor = os.getenv(f"LR_CAMINHO_{chave.upper()}") or valor
        if valor:
            caminhos[chave] = _resolver(valor, raiz)

    ind = cfg.get("indicadores_elabore")
    if isinstance(ind, dict):
        projeto = os.getenv("LR_ELABORE_PROJETO") or ind.get("projeto")
        if projeto:
            ind["projeto"] = _resolver(projeto, raiz)
    return cfg


def ler_config(raiz: Path | str) -> dict:
    """config.yaml já com os caminhos desta máquina. Use no lugar de abrir o config.yaml direto."""
    raiz = Path(raiz).resolve()
    with open(_arquivo_config(raiz), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return ajustar_config(cfg, raiz)
