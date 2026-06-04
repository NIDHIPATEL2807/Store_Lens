"""FastAPI app. Run with:  uvicorn app.main:app  or  python app/main.py"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from database           import init_db
from ingestion          import router as ingestion_router
from metrics            import router as metrics_router
from funnel             import router as funnel_router
from heatmap            import router as heatmap_router
from anomalies          import router as anomalies_router
from health             import router as health_router
from logging_middleware import LoggingMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Purplle Store Analytics API", version="1.0.0", lifespan=lifespan)

app.add_middleware(LoggingMiddleware)

for router in (
    ingestion_router, metrics_router, funnel_router,
    heatmap_router, anomalies_router, health_router,
):
    app.include_router(router)


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    import traceback, logging, json
    logging.getLogger("api").error(json.dumps({
        "error":     str(exc),
        "traceback": traceback.format_exc()[-500:],
    }))
    return JSONResponse({"error": "internal_server_error", "detail": str(exc)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=debug)
