"""Drain existing API work before restoring; hold new requests until it finishes."""
import asyncio
from contextlib import asynccontextmanager


class MaintenanceGate:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = 0
        self.restoring = False
        self.waiting = 0

    @asynccontextmanager
    async def access(self, exclusive=False):
        async with self.condition:
            if exclusive:
                self.waiting += 1
                try:
                    await self.condition.wait_for(lambda: self.active == 0 and not self.restoring)
                    self.restoring = True
                finally:
                    self.waiting -= 1
                    self.condition.notify_all()
            else:
                await self.condition.wait_for(lambda: not self.restoring and self.waiting == 0)
                self.active += 1
        try:
            yield
        finally:
            async with self.condition:
                if exclusive:
                    self.restoring = False
                else:
                    self.active -= 1
                self.condition.notify_all()


class MaintenanceMiddleware:
    """The local launcher runs one worker, so every API request shares this gate."""
    def __init__(self, app):
        self.app = app
        self.gate = MaintenanceGate()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        exclusive = scope["path"] == "/api/backups/restore" and scope["method"] == "POST"
        async with self.gate.access(exclusive):
            await self.app(scope, receive, send)
