"""Structured JSON logging middleware for FastAPI."""

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = str(uuid.uuid4())
        t0 = time.perf_counter()

        response = await call_next(request)

        latency = round((time.perf_counter() - t0) * 1000, 2)
        store_id = request.path_params.get("store_id")
        _logger.info(json.dumps({
            "trace_id":    trace_id,
            "store_id":    store_id,
            "endpoint":    request.url.path,
            "method":      request.method,
            "latency_ms":  latency,
            "status_code": response.status_code,
            "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }))
        response.headers["X-Trace-Id"] = trace_id
        return response
