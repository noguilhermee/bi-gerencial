# -*- coding: utf-8 -*-
"""
Script: sincronizar_sharepoint.py
Objetivo: espelhar as pastas de entrada (BD_SMARTQUESTION, BD_PROCAMPO) do SharePoint numa máquina sem OneDrive.

No notebook o OneDrive já mantém essas pastas em dia. No servidor (VPS) não há OneDrive: este módulo lê cada pasta
pelo link de compartilhamento (sharepoint.pastas no config.yaml) via Microsoft Graph, com as mesmas credenciais
TENANT_ID / CLIENT_ID / CLIENT_SECRET do baixar_base_procampo.py, e deixa a cópia local igual à do SharePoint:

  - baixa o que é novo ou mudou (data de alteração ou tamanho) e grava a data do SharePoint no arquivo local, porque
    os carregadores escolhem "o arquivo mais recente" pela data de modificação;
  - remove o que não existe mais lá (ex.: planilha movida para a pasta de histórico), para não ler a mesma visita em
    dois lugares. Se a listagem vier vazia ou bem menor que a cópia local, não remove nada (proteção contra falha).

Só roda com LR_SINCRONIZAR_SHAREPOINT=1 (no scripts/config/.env do servidor), para nunca gravar por cima da pasta que o
OneDrive sincroniza no notebook.

Uso isolado:
  python scripts/functions/sincronizar_sharepoint.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from functions.baixar_base_procampo import GRAPH, TOLERANCIA_S, _id_compartilhamento, _token, detectar_raiz
    from functions.config_projeto import ler_config
except ImportError:
    from baixar_base_procampo import GRAPH, TOLERANCIA_S, _id_compartilhamento, _token, detectar_raiz
    from config_projeto import ler_config

FRACAO_MINIMA_PARA_REMOVER = 0.5   # listagem remota com menos da metade dos arquivos locais = suspeita, não remove


def sincronizacao_ativa() -> bool:
    return os.getenv("LR_SINCRONIZAR_SHAREPOINT", "").strip().lower() in ("1", "true", "sim", "yes")


def _get(url: str, cabecalho: dict, **kwargs) -> dict:
    r = requests.get(url, headers=cabecalho, timeout=60, **kwargs)
    if r.status_code != 200:
        raise RuntimeError(f"Microsoft Graph respondeu HTTP {r.status_code} em {url.split('?')[0]}")
    return r.json()


def _listar(drive: str, item: str, cabecalho: dict, prefixo: str = "") -> dict[str, dict]:
    """Todos os arquivos abaixo da pasta, recursivo: {caminho relativo com '/': item do Graph}."""
    arquivos: dict[str, dict] = {}
    url = f"{GRAPH}/drives/{drive}/items/{item}/children?$top=200"
    while url:
        pagina = _get(url, cabecalho)
        for filho in pagina.get("value", []):
            nome = filho["name"]
            if nome.startswith("~$"):  # arquivo de bloqueio do Excel aberto
                continue
            caminho = f"{prefixo}{nome}"
            if "folder" in filho:
                arquivos.update(_listar(drive, filho["id"], cabecalho, caminho + "/"))
            elif "file" in filho:
                arquivos[caminho] = filho
        url = pagina.get("@odata.nextLink")
    return arquivos


def _data_remota(item: dict) -> float:
    return datetime.fromisoformat(item["lastModifiedDateTime"].replace("Z", "+00:00")).timestamp()


def _precisa_baixar(local: Path, item: dict) -> bool:
    if not local.is_file():
        return True
    st = local.stat()
    return st.st_size != item.get("size") or abs(_data_remota(item) - st.st_mtime) > TOLERANCIA_S


def _baixar(drive: str, item: dict, destino: Path, cabecalho: dict) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    url = item.get("@microsoft.graph.downloadUrl") or f"{GRAPH}/drives/{drive}/items/{item['id']}/content"
    tmp = Path(tempfile.NamedTemporaryFile(suffix=destino.suffix, dir=destino.parent, delete=False).name)
    try:
        cab = {} if "@microsoft.graph.downloadUrl" in item else cabecalho  # a URL pré-assinada dispensa o token
        with requests.get(url, headers=cab, stream=True, timeout=300) as r:
            if r.status_code != 200:
                raise RuntimeError(f"download de {destino.name} falhou (HTTP {r.status_code})")
            with open(tmp, "wb") as f:
                for pedaco in r.iter_content(chunk_size=1 << 20):
                    f.write(pedaco)
        if destino.suffix.lower() in (".xlsx", ".xlsm") and not zipfile.is_zipfile(tmp):
            raise RuntimeError(f"{destino.name} baixado não é um xlsx válido")
        os.replace(tmp, destino)
        momento = _data_remota(item)
        os.utime(destino, (momento, momento))
    finally:
        if tmp.exists():
            tmp.unlink()


def _upload_arquivo(drive: str, pasta_id: str, arquivo: Path, cabecalho: dict) -> None:
    """Envia um arquivo local para uma pasta no SharePoint (simples para <= 4MB, em pedaços para > 4MB)."""
    tamanho = arquivo.stat().st_size
    nome = arquivo.name
    if tamanho <= 4 * 1024 * 1024:
        put_url = f"{GRAPH}/drives/{drive}/items/{pasta_id}:/{nome}:/content"
        with open(arquivo, "rb") as f:
            res = requests.put(put_url, headers={**cabecalho, "Content-Type": "application/octet-stream"}, data=f, timeout=120)
        if res.status_code not in (200, 201):
            raise RuntimeError(f"upload de {nome} falhou (HTTP {res.status_code}): {res.text}")
    else:
        session_url = f"{GRAPH}/drives/{drive}/items/{pasta_id}:/{nome}:/createUploadSession"
        s_res = requests.post(session_url, headers=cabecalho, json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}, timeout=30)
        if s_res.status_code != 200:
            raise RuntimeError(f"criação de sessão de upload de {nome} falhou (HTTP {s_res.status_code})")
        upload_url = s_res.json()["uploadUrl"]
        chunk_size = 320 * 1024 * 10  # 3.2 MB (múltiplo de 320 KiB exigido pelo Graph)
        with open(arquivo, "rb") as f:
            start = 0
            while start < tamanho:
                chunk = f.read(chunk_size)
                end = start + len(chunk) - 1
                c_headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {start}-{end}/{tamanho}",
                }
                up_res = requests.put(upload_url, headers=c_headers, data=chunk, timeout=120)
                if up_res.status_code not in (200, 201, 202):
                    raise RuntimeError(f"chunk de {nome} falhou (HTTP {up_res.status_code}): {up_res.text}")
                start += len(chunk)


def enviar_indicadores_sharepoint(raiz: Path | None = None, gerados_desde: float | None = None) -> list[str]:
    """Envia as planilhas de indicadores geradas no servidor de volta para o SharePoint."""
    raiz = raiz or detectar_raiz()
    cfg = ler_config(raiz)
    if not sincronizacao_ativa():
        return []

    link = (cfg.get("sharepoint") or {}).get("pastas", {}).get("elabore_projeto")
    if not link:
        print("   ⚠️  sharepoint.pastas.elabore_projeto não configurado: indicadores não enviados ao SharePoint.")
        return []

    faltando = [v for v in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET") if not os.getenv(v)]
    if faltando:
        raise RuntimeError(f"variáveis ausentes em scripts/config/.env: {', '.join(faltando)}")

    cabecalho = {"Authorization": "Bearer " + _token(os.environ["TENANT_ID"], os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"])}
    raiz_item = _get(f"{GRAPH}/shares/{_id_compartilhamento(link)}/driveItem", cabecalho, params={"$select": "id,name,parentReference"})
    drive = raiz_item["parentReference"]["driveId"]
    item_id = raiz_item["id"]

    MAPEAMENTO = [
        ("elabore_mensal", "*indicadores_mensais.xlsx", "data/outputs/monthly"),
        ("elabore_anual", "*indicadores_anuais.xlsx", "data/outputs/annual"),
        ("elabore_forrageira", "*_base_custo_forrageira.xlsx", "data/outputs/forrageira"),
    ]

    enviados = []
    print("\n" + "=" * 70)
    print("☁️  ENVIANDO PLANILHAS DE INDICADORES GERADAS PARA O SHAREPOINT")
    print("=" * 70)

    for chave, padrao, subpasta_remota in MAPEAMENTO:
        caminho_cfg = cfg["caminhos"].get(chave)
        if not caminho_cfg:
            continue
        pasta_local = Path(caminho_cfg)
        if not pasta_local.is_dir():
            continue
        candidatos = [a for a in pasta_local.glob(padrao) if zipfile.is_zipfile(a)]
        if not candidatos:
            continue
        mais_recente = max(candidatos, key=lambda a: a.stat().st_mtime)
        if gerados_desde is not None and mais_recente.stat().st_mtime < (gerados_desde - 1):
            continue

        try:
            sub_resp = _get(f"{GRAPH}/drives/{drive}/items/{item_id}:/{subpasta_remota}", cabecalho, params={"$select": "id,name"})
            pasta_dest_id = sub_resp["id"]
            print(f"   ⬆️  Enviando {mais_recente.name} ({mais_recente.stat().st_size:,} bytes) -> {subpasta_remota}…")
            _upload_arquivo(drive, pasta_dest_id, mais_recente, cabecalho)
            print(f"      ✅ Enviado com sucesso!")
            enviados.append(mais_recente.name)
        except Exception as erro:
            print(f"   ⚠️  Não foi possível enviar {mais_recente.name} para o SharePoint: {erro}")

    return enviados


def baixar_indicadores_sharepoint(raiz: Path | None = None) -> list[str]:
    """Baixa as últimas planilhas de indicadores do SharePoint (usado com --sem-indicadores no servidor)."""
    raiz = raiz or detectar_raiz()
    cfg = ler_config(raiz)
    if not sincronizacao_ativa():
        return []

    link = (cfg.get("sharepoint") or {}).get("pastas", {}).get("elabore_projeto")
    if not link:
        return []

    faltando = [v for v in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET") if not os.getenv(v)]
    if faltando:
        return []

    cabecalho = {"Authorization": "Bearer " + _token(os.environ["TENANT_ID"], os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"])}
    raiz_item = _get(f"{GRAPH}/shares/{_id_compartilhamento(link)}/driveItem", cabecalho, params={"$select": "id,name,parentReference"})
    drive = raiz_item["parentReference"]["driveId"]
    item_id = raiz_item["id"]

    MAPEAMENTO = [
        ("elabore_mensal", "data/outputs/monthly"),
        ("elabore_anual", "data/outputs/annual"),
        ("elabore_forrageira", "data/outputs/forrageira"),
    ]

    baixados = []
    for chave, subpasta_remota in MAPEAMENTO:
        caminho_cfg = cfg["caminhos"].get(chave)
        if not caminho_cfg:
            continue
        pasta_local = Path(caminho_cfg)
        pasta_local.mkdir(parents=True, exist_ok=True)
        try:
            sub_resp = _get(f"{GRAPH}/drives/{drive}/items/{item_id}:/{subpasta_remota}", cabecalho, params={"$select": "id"})
            sub_id = sub_resp["id"]
            itens = _listar(drive, sub_id, cabecalho)
            for rel, item in itens.items():
                destino = pasta_local / rel
                if _precisa_baixar(destino, item):
                    _baixar(drive, item, destino, cabecalho)
                    baixados.append(destino.name)
        except Exception as erro:
            print(f"   ⚠️  Erro ao baixar indicadores de {subpasta_remota}: {erro}")

    if baixados:
        print(f"   ⬇️  {len(baixados)} planilha(s) de indicadores baixada(s) do SharePoint")
    return baixados


def sincronizar_pasta(link: str, pasta: Path, cabecalho: dict) -> tuple[int, int, int]:
    """Espelha uma pasta. Devolve (baixados, removidos, inalterados)."""
    raiz_item = _get(f"{GRAPH}/shares/{_id_compartilhamento(link)}/driveItem", cabecalho,
                     params={"$select": "id,name,folder,parentReference"})
    if "folder" not in raiz_item:
        raise RuntimeError(f"o link de {pasta.name} não aponta para uma pasta ('{raiz_item.get('name')}')")
    drive = raiz_item["parentReference"]["driveId"]
    remotos = _listar(drive, raiz_item["id"], cabecalho)
    print(f"   📁 {pasta.name}: {len(remotos)} arquivo(s) no SharePoint ('{raiz_item.get('name')}')")

    baixados = inalterados = 0
    for rel, item in sorted(remotos.items()):
        local = pasta / rel
        if _precisa_baixar(local, item):
            _baixar(drive, item, local, cabecalho)
            baixados += 1
            print(f"      ⬇️  {rel}")
        else:
            inalterados += 1

    locais = [a for a in pasta.rglob("*") if a.is_file()] if pasta.is_dir() else []
    sobrando = [a for a in locais if a.relative_to(pasta).as_posix() not in remotos]
    removidos = 0
    if sobrando:
        if not remotos or len(remotos) < FRACAO_MINIMA_PARA_REMOVER * len(locais):
            print(f"      ⚠️  {len(sobrando)} arquivo(s) local(is) fora do SharePoint, mas a listagem veio pequena "
                  f"demais ({len(remotos)} de {len(locais)}): nada foi removido.")
        else:
            for a in sobrando:
                a.unlink()
                removidos += 1
                print(f"      🗑️  {a.relative_to(pasta).as_posix()} (não existe mais no SharePoint)")
    return baixados, removidos, inalterados


def sincronizar_pastas_sharepoint(raiz: Path | None = None) -> bool:
    """Sincroniza todas as pastas de sharepoint.pastas. False = desligado nesta máquina (nada foi feito)."""
    raiz = raiz or detectar_raiz()
    cfg = ler_config(raiz)  # também carrega o .env do projeto
    if not sincronizacao_ativa():
        print("   ⏭️  LR_SINCRONIZAR_SHAREPOINT desligado: as pastas vêm do OneDrive desta máquina.")
        return False

    pastas = (cfg.get("sharepoint") or {}).get("pastas") or {}
    if not pastas:
        raise RuntimeError("sharepoint.pastas não configurado no config.yaml")
    faltando = [v for v in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET") if not os.getenv(v)]
    if faltando:
        raise RuntimeError(f"variáveis ausentes em scripts/config/.env: {', '.join(faltando)}")

    print("\n" + "=" * 70)
    print("☁️  SINCRONIZANDO AS PASTAS DE ENTRADA COM O SHAREPOINT")
    print("=" * 70)
    cabecalho = {"Authorization": "Bearer " + _token(os.environ["TENANT_ID"], os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"])}

    falhas = []
    for chave, link in pastas.items():
        if chave == "elabore_projeto":
            continue  # o projeto do elabore é sincronizado pelas funções de indicadores (monthly/annual/forrageira)
        destino = cfg["caminhos"].get(chave)
        if not destino:
            falhas.append(f"{chave}: sem caminhos.{chave} no config.yaml")
            continue
        try:
            b, r, i = sincronizar_pasta(link, Path(destino), cabecalho)
            print(f"   ✅ {chave}: {b} baixado(s), {r} removido(s), {i} já em dia")
        except Exception as erro:  # uma pasta com problema não impede as outras
            falhas.append(f"{chave}: {erro}")
            print(f"   ❌ {chave}: {erro}")
    if falhas:
        raise RuntimeError("; ".join(falhas) + " — a pipeline segue com a cópia local dessas pastas")
    return True


if __name__ == "__main__":
    try:
        sincronizar_pastas_sharepoint()
    except Exception as erro:
        print(f"❌ {erro}")
        sys.exit(1)
