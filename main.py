cd ~/Documentos/sabado-brn

cat >> handlers/http_handlers.py << 'EOF'


async def static_handler(request):
    """Serve qualquer arquivo de static/ (CSS, SVG, JS, imagens)."""
    nome = request.match_info.get("nome", "")
    if ".." in nome or nome.startswith("/"):
        return web.Response(status=403)
    caminho = os.path.join(DIR_STATIC, nome)
    if not os.path.isfile(caminho):
        return web.Response(status=404, text=f"nao encontrado: {nome}")
    return web.FileResponse(caminho)
EOF
