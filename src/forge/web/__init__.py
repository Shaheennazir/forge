"""Web-based TUI for Forge."""

import asyncio
from typing import Any, Dict
try:
    from aiohttp import web
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


class WebTUIHandler:
    """Handle WebSocket connections for web-based TUI."""
    
    def __init__(self):
        self.clients = set()
    
    async def handle_websocket(self, request):
        """Handle incoming WebSocket connection."""
        if not HAS_WEBSOCKETS:
            return web.Response(text="WebSocket support not installed", status=503)
        
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        
        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    # Process message and send response
                    response = {"status": "received", "data": msg.data}
                    await ws.send_json(response)
                elif msg.type == web.WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        
        return ws
    
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients."""
        for client in self.clients:
            try:
                await client.send_json(message)
            except:
                pass


def register_web_command(cli_app):
    """Register the web command with the CLI app."""
    import click
    
    @cli_app.command()
    @click.option('--host', default='localhost', help='Host to bind to')
    @click.option('--port', default=8080, type=int, help='Port to listen on')
    def web(host, port):
        """Launch the web-based TUI interface."""
        if not HAS_WEBSOCKETS:
            click.echo("WebSocket support not available. Install with: pip install aiohttp")
            return 1
        
        handler = WebTUIHandler()
        app = web.Application()
        app.router.add_get('/ws', handler.handle_websocket)
        app.router.add_get('/', lambda r: web.Response(text="<html><body><h1>Forge Web TUI</h1><script>const ws=new WebSocket('ws://'+location.host+'/ws');ws.onmessage=(e)=>console.log(e.data);setInterval(()=>ws.send(JSON.stringify({type:'ping'})),5000);</script></body></html>", content_type='text/html'))
        
        click.echo(f"Starting web server at http://{host}:{port}")
        web.run_app(app, host=host, port=port)
