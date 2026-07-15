from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._active_connections: set[WebSocket] = set()

    @property
    def active_count(self) -> int:
        return len(self._active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._active_connections.discard(websocket)

