# -*- coding: utf-8 -*-
"""
Pipeline COMPLETA do BI Labor Rural: do Elabore ao Supabase, num comando só.

Substitui o fluxo de duas pontas (gerar as planilhas no lr-indicadores-elabore à mão + executar_pipeline.py):

    1. [segundo plano] Notebook "Elabore Indicadores" (lr-indicadores-elabore) -> *_indicadores_mensais.xlsx,
                       *_indicadores_anuais.xlsx e *_base_custo_forrageira.xlsx
    0. [só no servidor, LR_SINCRONIZAR_SHAREPOINT=1] pastas BD_SMARTQUESTION e BD_PROCAMPO espelhadas do SharePoint
    2. base do Procampo (SharePoint), raw de visitas do SmartQuestion e raw do Procampo
                       (independentes do Elabore: rodam enquanto o passo 1 corre)
    3. aguarda o passo 1 e confere se as 3 planilhas são novas
    4. consistência mensal/anual -> elabore_raw_consistencia_*     (lê as planilhas do passo 1)
    5. notebook ETL_BI_LR.ipynb (SmartQuestion; lê a consistência do passo 4)
    6. fato econômica -> reconciliação -> fazendas do Procampo na dimensão -> raws de safra -> camada de consumo

Por que esta ordem: o notebook ETL_BI_LR.ipynb lê elabore_raw_consistencia_*, que só fica atual depois das planilhas
novas. Na pipeline antiga a sincronização rodava ANTES de existir planilha nova, então o BI sempre usava o Elabore
do dia anterior. Aqui a consistência é sincronizada depois da geração e antes do ETL.

Falhas: etapa crítica que falha (ou é pulada) encerra a pipeline com código 1. Etapa não crítica que falha vira
aviso e a pipeline segue com a última carga bem-sucedida; no fim o código é 2 ("concluída com avisos").
Se a geração dos indicadores falhar, as planilhas anteriores continuam sendo usadas e o aviso diz qual a idade delas.

Códigos de saída: 0 = tudo certo | 1 = falha crítica | 2 = concluída com avisos.

Uso:
    python scripts/executar_pipeline_completo.py                     # pipeline completa
    python scripts/executar_pipeline_completo.py --sem-indicadores   # usa as planilhas que já existem
    python scripts/executar_pipeline_completo.py --so-indicadores    # só gera as planilhas do Elabore
    python scripts/executar_pipeline_completo.py --sequencial        # sem sobreposição (passo 2 só depois do 1)
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import signal
import subprocess
import sys
import time
import traceback
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
warnings.filterwarnings("ignore", category=RuntimeWarning, module="zmq")

for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8")
    except Exception:
        pass

_aqui = Path(__file__).resolve().parent
RAIZ = _aqui.parent if _aqui.name.lower() == "scripts" else _aqui

for _p in (RAIZ, RAIZ / "scripts", RAIZ / "scripts" / "functions"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

NOTEBOOK_SQ = RAIZ / "scripts" / "ETL_BI_LR.ipynb"
TIMEOUT_CELULA_SQ = 1800

# (rótulo, chave de caminhos.* no config.yaml, padrão do arquivo) — os mesmos padrões que os carregadores usam.
PLANILHAS_ELABORE = (
    ("indicadores mensais", "elabore_mensal", "*indicadores_mensais.xlsx"),
    ("indicadores anuais", "elabore_anual", "*indicadores_anuais.xlsx"),
    ("base de custo de forrageira", "elabore_forrageira", "*_base_custo_forrageira.xlsx"),
)


# ==============================================================================
# EXECUÇÃO DE NOTEBOOKS (cada um num processo próprio, com kernel isolado)
# ==============================================================================
def _executar_notebook_interno(caminho: Path, cwd: Path, timeout: int,
                               salvar_em: Path | None, copia_falha: Path | None) -> int:
    """Roda dentro do subprocesso: executa o notebook célula a célula, parando no primeiro erro."""
    import nbformat
    from nbconvert.preprocessors import ExecutePreprocessor

    with open(caminho, "r", encoding="utf-8") as f:
        nb = nbformat.read(f, as_version=4)

    try:
        ExecutePreprocessor(timeout=timeout, kernel_name="python3").preprocess(nb, {"metadata": {"path": str(cwd)}})
    except Exception as erro:
        print(f"❌ ERRO NA EXECUÇÃO DO NOTEBOOK {caminho.name}: {erro}")
        if copia_falha is not None:
            # Só para diagnóstico: um único arquivo fixo, sobrescrito a cada falha (não acumula duplicatas).
            copia_falha.parent.mkdir(parents=True, exist_ok=True)
            with open(copia_falha, "w", encoding="utf-8") as f:
                nbformat.write(nb, f)
            print(f"   Cópia com as saídas até o erro: {copia_falha}")
        return 1

    if salvar_em is not None:
        # Regra 5 do AGENTS.md: grava no notebook original (sem _executado.ipynb). Via arquivo temporário para
        # nunca deixar o notebook pela metade se o processo for interrompido na gravação.
        tmp = salvar_em.with_name(salvar_em.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)
        tmp.replace(salvar_em)
    return 0


class ExecucaoNotebook:
    """Notebook rodando em subprocesso. Saída em arquivo de log próprio (se informado) ou herdada da pipeline."""

    def __init__(self, caminho: Path, cwd: Path, timeout_celula: int, salvar_em: Path | None = None,
                 copia_falha: Path | None = None, log: Path | None = None):
        cmd = [sys.executable, str(Path(__file__).resolve()), "--notebook-interno", str(caminho),
               "--cwd", str(cwd), "--timeout", str(timeout_celula)]
        if salvar_em:
            cmd += ["--salvar-em", str(salvar_em)]
        if copia_falha:
            cmd += ["--copia-falha", str(copia_falha)]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

        self.log = log
        self._arquivo_log = open(log, "w", encoding="utf-8") if log else None
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdout=self._arquivo_log, stderr=subprocess.STDOUT if self._arquivo_log else None,
            # Linux: grupo de processos próprio, para encerrar() levar junto o kernel do Jupyter
            start_new_session=(os.name != "nt"),
        )
        self.inicio = time.time()

    def aguardar(self, limite_total_s: float | None) -> int:
        """Espera terminar. Passou do limite total: encerra o processo (e o kernel filho) e levanta TimeoutError."""
        restante = None if limite_total_s is None else max(1.0, limite_total_s - (time.time() - self.inicio))
        try:
            return self.proc.wait(timeout=restante)
        except subprocess.TimeoutExpired:
            self.encerrar()
            raise TimeoutError(f"notebook passou do limite total de {int(limite_total_s)} s e foi encerrado")
        finally:
            if self._arquivo_log and self.proc.poll() is not None:
                self._arquivo_log.close()

    def encerrar(self) -> None:
        if self.proc.poll() is None:
            # Leva junto o kernel do Jupyter, que é processo filho do executor.
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        if self._arquivo_log and not self._arquivo_log.closed:
            self._arquivo_log.close()

    def final_do_log(self, linhas: int = 40) -> str:
        if not self.log or not self.log.is_file():
            return ""
        texto = self.log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join("      | " + ln for ln in texto[-linhas:])


# ==============================================================================
# CONFIG E CONFERÊNCIA DAS PLANILHAS DO ELABORE
# ==============================================================================
def carregar_config() -> dict:
    return _chamar("config_projeto", "ler_config", RAIZ)  # caminhos desta máquina (.env: LR_CAMINHO_*)


def config_indicadores(cfg: dict) -> dict:
    """Bloco indicadores_elabore; sem ele, deduz o projeto pela pasta das planilhas (…/data/outputs/<tipo>)."""
    bloco = dict(cfg.get("indicadores_elabore") or {})
    if not bloco.get("projeto"):
        bloco["projeto"] = str(Path(cfg["caminhos"]["elabore_mensal"]).parents[2])
    bloco.setdefault("notebook", "app/Elabore Indicadores.ipynb")
    bloco.setdefault("timeout_celula_segundos", 3600)
    bloco.setdefault("timeout_total_segundos", 10800)
    bloco.setdefault("idade_maxima_horas", 36)
    return bloco


def planilha_mais_recente(pasta: Path, padrao: str) -> Path | None:
    """Mesmo critério dos carregadores: o mais recente por data de modificação, ignorando xlsx incompleto."""
    if not pasta.is_dir():
        return None
    arquivos = [a for a in pasta.glob(padrao) if zipfile.is_zipfile(a)]
    return max(arquivos, key=lambda a: a.stat().st_mtime) if arquivos else None


def conferir_planilhas(cfg: dict, gerada_desde: float | None) -> list[str]:
    """Imprime a planilha que cada carga vai usar e devolve os problemas (ausente, antiga ou não regerada)."""
    limite_h = float(config_indicadores(cfg)["idade_maxima_horas"])
    problemas: list[str] = []
    print("   Planilhas do Elabore que as cargas vão usar:")
    for rotulo, chave, padrao in PLANILHAS_ELABORE:
        arquivo = planilha_mais_recente(Path(cfg["caminhos"][chave]), padrao)
        if arquivo is None:
            problemas.append(f"{rotulo}: nenhuma planilha '{padrao}' em {cfg['caminhos'][chave]}")
            print(f"   ❌ {rotulo}: nenhuma planilha encontrada")
            continue
        idade_h = (time.time() - arquivo.stat().st_mtime) / 3600
        nova = gerada_desde is not None and arquivo.stat().st_mtime >= gerada_desde
        situacao = "gerada agora" if nova else f"gerada há {idade_h:.1f} h"
        print(f"   {'✅' if nova or idade_h <= limite_h else '⚠️'} {rotulo}: {arquivo.name} ({situacao})")
        if gerada_desde is not None and not nova:
            problemas.append(f"{rotulo}: o notebook não gravou planilha nova (usando {arquivo.name}, {idade_h:.1f} h)")
        elif gerada_desde is None and idade_h > limite_h:
            problemas.append(f"{rotulo}: {arquivo.name} tem {idade_h:.1f} h (limite {limite_h:g} h)")
    return problemas


# ==============================================================================
# RELATÓRIO DE ETAPAS
# ==============================================================================
def _duracao(segundos: float) -> str:
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Pipeline:
    OK, AVISO, FALHA, PULADA = "OK", "AVISO", "FALHA", "PULADA"
    ICONES = {OK: "✅", AVISO: "⚠️ ", FALHA: "❌", PULADA: "⏭️ "}

    def __init__(self):
        self.resultados: list[tuple[str, str, str, float, str]] = []  # id, título, status, segundos, detalhe
        self.status: dict[str, str] = {}
        self.criticas: set[str] = set()
        self.interrompida = False

    def rodar(self, id_: str, titulo: str, fn, *, critica: bool = False, depende: tuple[str, ...] = (),
              desde: float | None = None) -> str:
        """Executa uma etapa. `depende`: ids que precisam ter terminado OK. `desde`: início real (etapa em segundo plano)."""
        if critica:
            self.criticas.add(id_)
        print("\n" + "=" * 70)
        print(f"▶ {titulo}")
        print("=" * 70)

        motivo = None
        if self.interrompida:
            motivo = "pipeline interrompida por falha crítica anterior"
        else:
            pendentes = [d for d in depende if self.status.get(d) != self.OK]
            if pendentes:
                motivo = f"depende de etapa sem sucesso: {', '.join(pendentes)}"
        if motivo:
            print(f"⏭️  Pulada: {motivo}")
            return self._registrar(id_, titulo, self.PULADA, 0.0, motivo)

        inicio = desde if desde is not None else time.time()
        try:
            fn()
        except (Exception, SystemExit) as erro:  # alguns carregadores encerram com sys.exit
            if not isinstance(erro, (RuntimeError, TimeoutError)):
                traceback.print_exc()
            status = self.FALHA if critica else self.AVISO
            print(f"{self.ICONES[status]} {titulo}: {erro}")
            if critica:
                self.interrompida = True
            return self._registrar(id_, titulo, status, time.time() - inicio, str(erro).splitlines()[0] if str(erro) else type(erro).__name__)
        return self._registrar(id_, titulo, self.OK, time.time() - inicio, "")

    def _registrar(self, id_, titulo, status, segundos, detalhe) -> str:
        self.status[id_] = status
        self.resultados.append((id_, titulo, status, segundos, detalhe))
        return status

    def codigo_saida(self) -> int:
        if any(s in (self.FALHA, self.PULADA) and i in self.criticas for i, s in self.status.items()):
            return 1
        return 2 if any(s == self.AVISO for s in self.status.values()) else 0

    def imprimir_resumo(self, inicio: float) -> None:
        print("\n" + "=" * 70)
        print("RESUMO DA EXECUÇÃO")
        print("=" * 70)
        for id_, titulo, status, segundos, detalhe in self.resultados:
            extra = f"  — {detalhe}" if detalhe else ""
            print(f" {self.ICONES[status]} {_duracao(segundos):>8}  {titulo}{extra}")
        print("-" * 70)
        fim = time.time()
        print(f" Início:  {datetime.fromtimestamp(inicio).strftime('%d/%m/%Y %H:%M:%S')}")
        print(f" Fim:     {datetime.fromtimestamp(fim).strftime('%d/%m/%Y %H:%M:%S')}")
        print(f" ⏱️  DURAÇÃO TOTAL: {_duracao(fim - inicio)}")
        codigo = self.codigo_saida()
        if codigo == 0:
            print("🎉 Pipeline completa finalizada com sucesso!")
        elif codigo == 2:
            print("⚠️  Pipeline concluída COM AVISOS: veja as etapas marcadas acima (dados anteriores foram mantidos).")
        else:
            print("💥 Pipeline INTERROMPIDA por falha crítica.")


def _chamar(modulo: str, funcao: str, *args, **kwargs):
    """Importa scripts/functions/<modulo>.py só no momento de usar (um módulo quebrado não derruba o resto)."""
    return getattr(importlib.import_module(f"functions.{modulo}"), funcao)(*args, **kwargs)


# ==============================================================================
# PIPELINE
# ==============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="Pipeline completa do BI Labor Rural (Elabore -> Supabase).")
    ap.add_argument("--sem-indicadores", action="store_true", help="não gera as planilhas do Elabore; usa as mais recentes")
    ap.add_argument("--so-indicadores", action="store_true", help="só gera e confere as planilhas do Elabore")
    ap.add_argument("--sequencial", action="store_true", help="não sobrepõe as raws de visitas/Procampo à geração dos indicadores")
    # uso interno: o próprio script roda um notebook num subprocesso
    ap.add_argument("--notebook-interno", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--cwd", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=int, default=1800, help=argparse.SUPPRESS)
    ap.add_argument("--salvar-em", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--copia-falha", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.notebook_interno:
        return _executar_notebook_interno(args.notebook_interno, args.cwd, args.timeout, args.salvar_em, args.copia_falha)
    if args.sem_indicadores and args.so_indicadores:
        ap.error("--sem-indicadores e --so-indicadores não fazem sentido juntos")

    inicio_geral = time.time()
    cfg = carregar_config()
    ind = config_indicadores(cfg)
    logs = RAIZ / "data" / "output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    pipe = Pipeline()

    print("=" * 70)
    print("🚀 PIPELINE COMPLETA — BI LABOR RURAL")
    print(f"⏰ Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 70)

    # 0. Pastas de entrada (só no servidor; no notebook o OneDrive já sincroniza) ------------------------
    if _chamar("sincronizar_sharepoint", "sincronizacao_ativa"):
        pipe.rodar("sharepoint", "Pastas de entrada (SharePoint -> BD_SMARTQUESTION, BD_PROCAMPO)",
                   lambda: _chamar("sincronizar_sharepoint", "sincronizar_pastas_sharepoint", RAIZ))

    gerar = not args.sem_indicadores
    execucao_ind: ExecucaoNotebook | None = None
    inicio_ind = [0.0]

    def iniciar_indicadores() -> None:
        nonlocal execucao_ind
        nb = Path(ind["projeto"]) / ind["notebook"]
        if not nb.is_file():
            raise RuntimeError(f"notebook do Elabore não encontrado: {nb}")
        inicio_ind[0] = time.time()
        print(f"   Notebook: {nb}")
        print(f"   Log: {logs / (carimbo + '_elabore_indicadores.log')}")
        execucao_ind = ExecucaoNotebook(
            nb, nb.parent, int(ind["timeout_celula_segundos"]),
            copia_falha=logs / "ultima_falha_elabore_indicadores.ipynb",
            log=logs / f"{carimbo}_elabore_indicadores.log",
        )

    def concluir_indicadores() -> None:
        """Espera o notebook (se foi iniciado), depois confere as planilhas. Qualquer problema vira aviso."""
        problemas: list[str] = []
        if execucao_ind is not None:
            print("   Aguardando o notebook de indicadores…")
            try:
                rc = execucao_ind.aguardar(float(ind["timeout_total_segundos"]))
            except TimeoutError as erro:
                problemas.append(str(erro))
                rc = None
            if rc not in (0, None):
                problemas.append(f"notebook terminou com código {rc}")
            if rc != 0:
                print("   Final do log do notebook:\n" + execucao_ind.final_do_log())
        problemas += conferir_planilhas(cfg, inicio_ind[0] if execucao_ind is not None and not problemas else None)
        if problemas:
            raise RuntimeError("; ".join(problemas) + " — a pipeline segue com as planilhas anteriores")
        if _chamar("sincronizar_sharepoint", "sincronizacao_ativa"):
            try:
                _chamar("sincronizar_sharepoint", "enviar_indicadores_sharepoint", RAIZ, inicio_ind[0])
            except Exception as erro_up:
                print(f"   ⚠️  Aviso ao enviar indicadores para o SharePoint: {erro_up}")

    try:
        # 1. Geração dos indicadores (segundo plano) ---------------------------------------------------------
        if gerar and args.sequencial:
            pipe.rodar("indicadores", "Gerar indicadores do Elabore (mensal, anual e forrageira)",
                       lambda: (iniciar_indicadores(), concluir_indicadores()))
        elif gerar:
            print("\n🧮 Gerando os indicadores do Elabore em segundo plano (as raws independentes rodam junto)…")
            try:
                iniciar_indicadores()
            except Exception as erro:
                print(f"⚠️  Não foi possível iniciar a geração dos indicadores: {erro}")
                pipe._registrar("indicadores", "Gerar indicadores do Elabore (mensal, anual e forrageira)",
                                pipe.AVISO, 0.0, str(erro))
                gerar = False

        # 2. Raws independentes do Elabore -------------------------------------------------------------------
        if not args.so_indicadores:
            pipe.rodar("raw_visitas", "Raw de visitas do SmartQuestion (sq_raw_visitas)",
                       lambda: _chamar("carregar_historico_visitas", "executar_carga_historico_visitas", RAIZ))
            pipe.rodar("base_procampo", "Base de produtores do Procampo (SharePoint -> BD_PROCAMPO)",
                       lambda: _chamar("baixar_base_procampo", "sincronizar_base_procampo", RAIZ))
            pipe.rodar("raw_procampo", "Raw do Procampo (pro_raw_fazendas + pro_raw_visitas)",
                       lambda: _chamar("carregar_procampo", "executar_carga_procampo", RAIZ))

        # 3. Fecha a geração dos indicadores ------------------------------------------------------------------
        if gerar and not args.sequencial:
            pipe.rodar("indicadores", "Gerar indicadores do Elabore (mensal, anual e forrageira)",
                       concluir_indicadores, desde=inicio_ind[0])
        elif not gerar and args.sem_indicadores:
            if _chamar("sincronizar_sharepoint", "sincronizacao_ativa"):
                _chamar("sincronizar_sharepoint", "baixar_indicadores_sharepoint", RAIZ)
            pipe.rodar("indicadores", "Conferir planilhas do Elabore (sem gerar)", lambda: _exigir(conferir_planilhas(cfg, None)))

        if args.so_indicadores:
            return _encerrar(pipe, inicio_geral)

        # 4. Consistência -> ETL SmartQuestion ----------------------------------------------------------------
        def sincronizar_consistencia() -> None:
            if _chamar("atualizar_detalhamento_consistencia", "executar_sincronizacao_consistencia", RAIZ) is False:
                raise RuntimeError("sincronização retornou falha; o ETL segue com o que já está em elabore_raw_consistencia_*")

        pipe.rodar("consistencia", "Consistência mensal e anual -> elabore_raw_consistencia_*", sincronizar_consistencia)

        def etl_smartquestion() -> None:
            execucao = ExecucaoNotebook(NOTEBOOK_SQ, NOTEBOOK_SQ.parent, TIMEOUT_CELULA_SQ, salvar_em=NOTEBOOK_SQ)
            rc = execucao.aguardar(None)
            if rc != 0:
                raise RuntimeError(f"ETL_BI_LR.ipynb terminou com código {rc}")

        pipe.rodar("etl_sq", "ETL SmartQuestion (ETL_BI_LR.ipynb)", etl_smartquestion, critica=True)

        # 5. Pós-ETL -------------------------------------------------------------------------------------------
        pipe.rodar("economico", "Fato econômica (tab_fato_economico)",
                   lambda: _chamar("carregar_fato_economico", "processar_e_carregar_fato_economico", RAIZ), critica=True)
        # A reconciliação apaga e reconstrói cada mês de tab_dim_fazendas_ativas só com o SmartQuestion:
        # as fazendas do Procampo precisam ser republicadas logo depois dela.
        pipe.rodar("reconciliacao", "Reconciliação de movimentações e ativos",
                   lambda: _chamar("reconciliar_movimentacao_e_ativos", "executar_reconciliacao", reindex_completo=True),
                   critica=True)
        pipe.rodar("dim_procampo", "Fazendas do Procampo em tab_dim_fazendas_ativas",
                   lambda: _chamar("publicar_dim_procampo", "executar_publicacao_dim_procampo", RAIZ), critica=True)
        pipe.rodar("safra", "Raws de safra (base de custo de forrageira)",
                   lambda: _chamar("carregar_safra_forrageira", "executar_carga_safra_forrageira", RAIZ))
        pipe.rodar("consumo", "Camada de consumo (tab_fato_*)",
                   lambda: _chamar("camada_consumo", "executar_camada_consumo", RAIZ, gravar=True),
                   critica=True, depende=("raw_visitas",))
    finally:
        if execucao_ind is not None:
            execucao_ind.encerrar()  # não deixa o notebook do Elabore órfão se a pipeline cair no meio

    return _encerrar(pipe, inicio_geral)


def _exigir(problemas: list[str]) -> None:
    if problemas:
        raise RuntimeError("; ".join(problemas))


def _registrar_historico(pipe: Pipeline, inicio: float) -> None:
    """Uma linha por execução em data/output/logs/historico_execucoes.csv (abre no Excel): acompanha a duração ao longo do tempo."""
    caminho = RAIZ / "data" / "output" / "logs" / "historico_execucoes.csv"
    try:
        novo = not caminho.is_file()
        fim = time.time()
        campos = [
            datetime.fromtimestamp(inicio).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.fromtimestamp(fim).strftime("%Y-%m-%d %H:%M:%S"),
            _duracao(fim - inicio),
            str(int(fim - inicio)),
            {0: "SUCESSO", 1: "FALHA", 2: "AVISOS"}[pipe.codigo_saida()],
        ] + [f"{_duracao(seg)}" for _, _, _, seg, _ in pipe.resultados]
        cabecalho = ["inicio", "fim", "duracao", "duracao_segundos", "resultado"] + [i for i, *_ in pipe.resultados]
        with open(caminho, "a", encoding="utf-8-sig", newline="") as f:
            if novo:
                f.write(";".join(cabecalho) + "\n")
            f.write(";".join(campos) + "\n")
        print(f" Histórico de durações: {caminho}")
    except Exception as erro:  # nunca derruba a pipeline por causa do histórico
        print(f" ⚠️  Não foi possível gravar o histórico de durações: {erro}")


def _encerrar(pipe: Pipeline, inicio: float) -> int:
    pipe.imprimir_resumo(inicio)
    _registrar_historico(pipe, inicio)
    return pipe.codigo_saida()


if __name__ == "__main__":
    sys.exit(main())
