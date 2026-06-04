"""
Structured JSON logging for every request.
Registers before_request / after_request hooks on the Flask app.
"""

import json
import logging
import time
import uuid
from flask import Flask, g, request

_logger = logging.getLogger("api")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def register(app: Flask) -> None:
    @app.before_request
    def _before():
        g.trace_id   = str(uuid.uuid4())
        g.start_time = time.perf_counter()

    @app.after_request
    def _after(response):
        latency = round((time.perf_counter() - g.get("start_time", time.perf_counter())) * 1000, 2)
        store_id = request.view_args.get("store_id") if request.view_args else None
        record = {
            "trace_id":    g.get("trace_id", ""),
            "store_id":    store_id,
            "endpoint":    request.path,
            "method":      request.method,
            "latency_ms":  latency,
            "status_code": response.status_code,
            "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _logger.info(json.dumps(record))
        response.headers["X-Trace-Id"] = g.get("trace_id", "")
        return response

    @app.errorhandler(Exception)
    def _unhandled(err):
        import traceback
        _logger.error(json.dumps({
            "trace_id":  g.get("trace_id", ""),
            "error":     str(err),
            "traceback": traceback.format_exc()[-500:],
        }))
        return {"error": "internal_server_error", "detail": str(err)}, 500
