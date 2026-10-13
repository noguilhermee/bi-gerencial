@echo off
setlocal enabledelayedexpansion

:: ============================================================================
:: Pipeline COMPLETA LR (Elabore -> SmartQuestion -> Supabase) - Agendador de Tarefas do Windows
:: Gera os indicadores do Elabore e executa a pipeline de ponta a ponta.
:: Codigos de saida: 0 = sucesso | 1 = falha critica | 2 = concluida com avisos
:: ============================================================================

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJETO_DIR=%%~fI"

if exist "C:\ProgramData\anaconda3\python.exe" (
    set "PYTHON_EXE=C:\ProgramData\anaconda3\python.exe"
) else (
    set "PYTHON_EXE=python"
)

set "SCRIPT_PATH=%PROJETO_DIR%\scripts\executar_pipeline_completo.py"
set "LOGS_DIR=%PROJETO_DIR%\data\output\logs"

:: Garantir pasta de logs
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

:: Gerar timestamp formatado YYYY_MM_DD_HHMMSS via powershell
for /f "usebackq tokens=*" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy_MM_dd_HHmmss'"`) do set "TIMESTAMP=%%i"

if not defined TIMESTAMP (
    set "TIMESTAMP=%DATE:/=_%_%TIME::=_%"
    set "TIMESTAMP=%TIMESTAMP: =0%"
    set "TIMESTAMP=%TIMESTAMP:,=%"
)

set "LOG_FILE=%LOGS_DIR%\%TIMESTAMP%_execucao_completa.log"

echo ============================================================================ >> "%LOG_FILE%"
echo [INICIO] EXECUCAO AGENDADA: LR PIPELINE COMPLETA >> "%LOG_FILE%"
echo Data/Hora: %date% %time% >> "%LOG_FILE%"
echo Diretorio: %PROJETO_DIR% >> "%LOG_FILE%"
echo ============================================================================ >> "%LOG_FILE%"

cd /d "%PROJETO_DIR%"
"%PYTHON_EXE%" "%SCRIPT_PATH%" >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo. >> "%LOG_FILE%"
echo ============================================================================ >> "%LOG_FILE%"
if %EXIT_CODE% equ 0 (
    echo [SUCESSO] PIPELINE COMPLETA CONCLUIDA COM SUCESSO! (Codigo: %EXIT_CODE%^) >> "%LOG_FILE%"
) else if %EXIT_CODE% equ 2 (
    echo [AVISO] PIPELINE CONCLUIDA COM AVISOS - veja o RESUMO DA EXECUCAO acima (Codigo: %EXIT_CODE%^) >> "%LOG_FILE%"
) else (
    echo [ERRO] ERRO NA EXECUCAO DA PIPELINE! (Codigo: %EXIT_CODE%^) >> "%LOG_FILE%"
)
echo Finalizado em: %date% %time% >> "%LOG_FILE%"
echo ============================================================================ >> "%LOG_FILE%"

exit /b %EXIT_CODE%
