"""Small ASGI request-body limiter with per-route size budgets."""
import json


class PathAwareRequestBodyLimitMiddleware:
    def __init__(self, app, *, default_max_body_size: int, path_limits: dict[str, int] | None = None):
        self.app = app
        self.default_max_body_size = default_max_body_size
        self.path_limits = dict(path_limits or {})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.path_limits.get(scope.get("path", ""), self.default_max_body_size)
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length) > limit:
                    await self._too_large(send)
                    return
            except ValueError:
                await self._too_large(send)
                return

        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            total += len(message.get("body", b""))
            if total > limit:
                await self._too_large(send)
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _too_large(send):
        body = json.dumps({"detail": "Request body exceeds the allowed size"}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
