"""Tests for per-route request body size limits."""
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from request_body_limit import PathAwareRequestBodyLimitMiddleware


class RequestBodyLimitTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()

        @app.post("/default")
        async def default_endpoint(request: Request):
            return {"size": len(await request.body())}

        @app.post("/archive")
        async def archive_endpoint(request: Request):
            return {"size": len(await request.body())}

        async def pass_through(request, call_next):
            return await call_next(request)

        app.add_middleware(
            PathAwareRequestBodyLimitMiddleware,
            default_max_body_size=4,
            path_limits={"/archive": 8},
        )
        app.add_middleware(BaseHTTPMiddleware, dispatch=pass_through)
        self.client = TestClient(app)

    def test_default_routes_keep_the_small_request_limit(self):
        self.assertEqual(self.client.post("/default", content=b"1234").status_code, 200)
        response = self.client.post("/default", content=b"12345")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "Request body exceeds the allowed size")

    def test_archive_route_uses_its_explicit_larger_limit(self):
        self.assertEqual(self.client.post("/archive", content=b"12345678").status_code, 200)
        response = self.client.post("/archive", content=b"123456789")
        self.assertEqual(response.status_code, 413)

    def test_chunked_request_is_limited_without_content_length(self):
        # The ASGI middleware enforces the byte count even if no Content-Length is supplied.
        response = self.client.post("/archive", content=b"123456789", headers={"Transfer-Encoding": "chunked"})
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
