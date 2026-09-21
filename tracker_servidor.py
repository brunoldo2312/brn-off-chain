# tracker_servidor.py — Servidor Tracker Centralizado para Descoberta Automática de IPs RWA
import time
from flask import Flask, jsonify, request

app = Flask(__name__)

# Armazena os nós ativos na rede { "ip:porta": timestamp_ultimo_ping }
ACTIVE_NODES = {}
NODE_TIMEOUT = 45  # Remove nós que sumirem por mais de 45 segundos

@app.route("/api/ping", methods=["POST"])
def ping_node():
    """Nós de qualquer residência batem aqui para registrar seu IP público de internet"""
    data = request.get_json(silent=True) or {}
    p2p_port = data.get("p2p_port", 7777)
    
    # Captura o IP público real de onde veio a requisição de internet
    remote_ip = request.remote_addr
    if remote_ip == "127.0.0.1":
        # Se testado localmente na mesma máquina, aceita o IP local informado
        remote_ip = data.get("local_ip", "127.0.0.1")
        
    endpoint = f"{remote_ip}:{p2p_port}"
    ACTIVE_NODES[endpoint] = time.time()
    
    # Limpa nós antigos que ficaram offline
    now = time.time()
    for k in list(ACTIVE_NODES.keys()):
        if now - ACTIVE_NODES[k] > NODE_TIMEOUT:
            del ACTIVE_NODES[k]
            
    # Retorna a lista de todos os outros computadores descobertos na internet
    other_peers = [node for node in ACTIVE_NODES.keys() if node != endpoint]
    return jsonify({"ok": True, "peers": other_peers})

if __name__ == "__main__":
    print("🚀 Servidor Tracker de Descoberta P2P Online na porta 6000...")
    app.run(host="0.0.0.0", port=6000, debug=False)
