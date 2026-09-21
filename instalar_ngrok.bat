@echo off
setlocal

echo ==== 1. Verificando se o Ngrok esta instalado ====
where ngrok >nul 2>nul
if errorlevel 1 (
    echo Ngrok nao encontrado. Instalando via winget...
    winget install --id ngrok.ngrok --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo Falha ao instalar automaticamente.
        echo Baixe manualmente em: https://ngrok.com/download
        echo Descompacte ngrok.exe em C:\Windows\System32 ou adicione ao PATH.
        pause
        exit /b 1
    )
)

echo ==== 2. Configurando authtoken ====
set "NGROK_AUTHTOKEN=COLE_AQUI_SEU_TOKEN_PUBLICO"
ngrok config add-authtoken %NGROK_AUTHTOKEN%

echo ==== 3. Testando instalacao ====
ngrok version

echo.
echo Pronto. Agora rode iniciar_brn.bat para subir o no + tunel.
pause
