#!/usr/bin/env python3
"""
run.py — setup completo em um comando.
Requer instal.py na mesma pasta.
Uso: python run.py
"""
from __future__ import annotations
import os
import pathlib
import platform
import re
import secrets
import subprocess
import sys
import venv

IS_WINDOWS = platform.system() == "Windows"
INSTALLER = "instal.py"   # nome real do arquivo (minúsculo)


def step(msg: str) -> None:
    print(f"\n=== {msg} ===")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent
    os.chdir(here)

    installer = here / INSTALLER
    if not installer.exists():
        print(f"ERRO: {INSTALLER} não encontrado em {here}", file=sys.stderr)
        return 1

    # 1. Gera o projeto
    step(f"Rodando {INSTALLER}")
    run([sys.executable, str(installer)])

    project = here / "brn-prod"
    if not project.exists():
        print(f"ERRO: {project} não foi criado.", file=sys.stderr)
        return 1
    os.chdir(project)

    # 2. .env com segredos gerados
    step("Criando .env com segredos aleatórios")
    env_path = pathlib.Path(".env")
    if not env_path.exists():
        env_path.write_text(pathlib.Path(".env.example").read_text())
    txt = env_path.read_text()
    if re.search(r"BRN_NETWORK_KEY=\s*$", txt, re.M):
        txt = re.sub(r"BRN_NETWORK_KEY=.*",
                     "BRN_NETWORK_KEY=" + secrets.token_hex(32), txt)
    if re.search(r"BRN_API_TOKEN=\s*$", txt, re.M):
        txt = re.sub(r"BRN_API_TOKEN=.*",
                     "BRN_API_TOKEN=" + secrets.token_urlsafe(32), txt)
    env_path.write_text(txt)
    if not IS_WINDOWS:
        os.chmod(env_path, 0o600)
    print(f"  .env criado ({env_path})")

    # 3. venv
    step("Criando ambiente virtual (.venv)")
    if not pathlib.Path(".venv").exists():
        venv.create(".venv", with_pip=True)

    py = (".venv\\Scripts\\python.exe" if IS_WINDOWS
          else ".venv/bin/python")
    py_path = pathlib.Path(py)
    if not py_path.exists():
        print(f"ERRO: {py_path} não existe.", file=sys.stderr)
        return 1
    py_str = str(py_path)

    # 4. dependências
    step("Instalando dependências (pode levar 1-3 min)")
    run([py_str, "-m", "pip", "install", "-q", "--upgrade", "pip"])
    run([py_str, "-m", "pip", "install", "-q",
         "-r", "requirements-dev.txt"])

    # 5. testes
    step("Rodando testes")
    r = subprocess.run([py_str, "-m", "pytest", "-q"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        print("  ✓ todos os testes passaram")
    else:
        print("  ⚠ testes falharam (segue mesmo assim):")
        print(r.stdout[-500:])
        print(r.stderr[-500:])

    # 6. HD wallet
    step("Gerando carteira HD")
    r = subprocess.run([py_str, "-m", "brn.cli", "hd-new"],
                       capture_output=True, text=True, check=True)
    print(r.stdout)
    pathlib.Path("mnemonic.txt").write_text(r.stdout)
    if not IS_WINDOWS:
        os.chmod("mnemonic.txt", 0o600)
    m = re.search(r"(brn1[a-f0-9]{40})", r.stdout)
    if not m:
        print("ERRO: endereço não encontrado em hd-new", file=sys.stderr)
        return 1
    addr = m.group(1)
    print(f"  endereço: {addr}")
    print("  mnemônico salvo em mnemonic.txt (MANTENHA SEGURO)")

    # 7. genesis
    step("Inicializando cadeia")
    run([py_str, "-m", "brn.cli", "init", addr])

    # 8. minera 3 blocos
    step("Minerando 3 blocos de demonstração")
    for i in range(3):
        print(f"  bloco {i+1}/3…")
        run([py_str, "-m", "brn.cli", "mine", addr])

    # 9. pré-cria o token de API antes de exibir (o nó também cria,
    #    mas só depois de subir — precisamos dele agora)
    step("Preparando token da API")
    data_dir = pathlib.Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    tok_file = data_dir / ".api_token"
    if not tok_file.exists():
        tok_file.write_text(secrets.token_urlsafe(32))
        if not IS_WINDOWS:
            os.chmod(tok_file, 0o600)
    api_token = tok_file.read_text().strip()
    print(f"  token salvo em {tok_file}")

    # 10. subir nó + explorador
    step("Subindo nó + explorador")
    print(f"\n  API token: {api_token}")
    print("  Home:  http://127.0.0.1:8080/")
    print("  API:   http://127.0.0.1:8080/api/status")
    print(f"  Teste: curl -H 'X-API-Token: {api_token}' "
          "http://127.0.0.1:8080/api/status")
    print("\n  Para habilitar NGROK, edite .env e rode:")
    print("    python -m brn.cli node --with-explorer --ngrok")
    print("\n  Ctrl+C para encerrar.\n")

    try:
        run([py_str, "-m", "brn.cli", "node", "--with-explorer"])
    except KeyboardInterrupt:
        print("\nEncerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
