@echo off
setlocal
cd /d "%~dp0"

REM ==== Token publico do Ngrok ====
set "NGROK_AUTHTOKEN=3J8xHeVX46aXrOZeXnKrVVFMLTr"

REM ==== Definições de Ambiente para o nó ====
set "BRN_WEB_PORT=5000"
set "BRN_USE_NGROK=1"

REM ==== Sobe o nó completo usando o caminho absoluto do Python ====
start "BRN Node Explorer" cmd /k "C:\Users\mayra\AppData\Local\Programs\Python\Python313\python.exe" explorer.py

pause
