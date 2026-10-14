#!/usr/bin/env bash
# ==============================================================================
# Pipeline COMPLETA LR (Elabore -> SmartQuestion -> Supabase) - Servidor Linux (VPS)
# Execução agendada via Cron ou manual no servidor.
# Códigos de saída: 0 = sucesso | 1 = falha crítica | 2 = concluída com avisos
# ==============================================================================

set -u

# Diretório raiz do projeto (um nível acima de scripts/)
DIR_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR_PROJETO="$(cd "$DIR_SCRIPT/.." && pwd)"

# Localizar o Python do ambiente virtual (se existir) ou o padrão do sistema
if [ -f "$DIR_PROJETO/venv/bin/python3" ]; then
    PYTHON_EXE="$DIR_PROJETO/venv/bin/python3"
elif [ -f "/home/ubuntu/venv/bin/python3" ]; then
    PYTHON_EXE="/home/ubuntu/venv/bin/python3"
else
    PYTHON_EXE="$(which python3)"
fi

SCRIPT_PATH="$DIR_PROJETO/scripts/executar_pipeline_completo.py"
LOGS_DIR="$DIR_PROJETO/data/output/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +"%Y_%m_%d_%H%M%S")"
LOG_FILE="$LOGS_DIR/${TIMESTAMP}_execucao_servidor.log"

export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"
export TZ="America/Sao_Paulo"

echo "============================================================================" >> "$LOG_FILE"
echo "[INICIO] EXECUCAO AGENDADA NO SERVIDOR: LR PIPELINE COMPLETA" >> "$LOG_FILE"
echo "Data/Hora: $(date)" >> "$LOG_FILE"
echo "Diretório: $DIR_PROJETO" >> "$LOG_FILE"
echo "Python:    $PYTHON_EXE" >> "$LOG_FILE"
echo "============================================================================" >> "$LOG_FILE"

cd "$DIR_PROJETO" || exit 1

"$PYTHON_EXE" "$SCRIPT_PATH" "$@" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "" >> "$LOG_FILE"
echo "============================================================================" >> "$LOG_FILE"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[SUCESSO] PIPELINE CONCLUIDA COM SUCESSO! (Código: $EXIT_CODE)" >> "$LOG_FILE"
elif [ $EXIT_CODE -eq 2 ]; then
    echo "[AVISO] PIPELINE CONCLUIDA COM AVISOS (Código: $EXIT_CODE)" >> "$LOG_FILE"
else
    echo "[ERRO] FALHA CRITICA NA EXECUCAO DA PIPELINE! (Código: $EXIT_CODE)" >> "$LOG_FILE"
fi
echo "Finalizado em: $(date)" >> "$LOG_FILE"
echo "============================================================================" >> "$LOG_FILE"

exit $EXIT_CODE
