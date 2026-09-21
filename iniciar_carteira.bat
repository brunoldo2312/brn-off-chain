@echo off
title BRN RWA - Inicializador da Carteira Grafica
clis
echo ============================================================
echo   INICIALIZANDO CARTEIRA DIGITAL BRN MULTI-CHAIN RWA
echo ============================================================
echo.

REM Executa a carteira grafica usando o comando py global do sistema
py app_wallet.py

if %errorlevel% neq 0 (
    echo.
    echo [ERRO] Nao foi possivel abrir a carteira. 
    echo Certifique-se de que instalou os pacotes: py -m pip install pywebview requests
    pause
)
