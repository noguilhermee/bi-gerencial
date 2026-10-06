@echo off
title BI Labor Rural - Dashboard Kiosk
echo ====================================================
echo   BI LABOR RURAL — INICIANDO MODO KIOSK PARA TV
echo ====================================================
echo.

set VERCEL_URL=https://bi-labor-rural.vercel.app

echo Abrindo Chrome em modo fullscreen kiosk...
start chrome --kiosk --disable-infobars --disable-session-crashed-bubble --noerrdialogs "%VERCEL_URL%"

echo.
echo Dashboard aberto em modo Kiosk na TV.
echo Para fechar o modo Kiosk na TV, pressione Alt + F4 ou Alt + Tab.
pause
