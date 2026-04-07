import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app import database
from app.config import settings
from app.routes.analytics import router as analytics_router
from app.routes.benchmark import router as benchmark_router
from app.routes.messages import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield
    await database.close_db()


app = FastAPI(
    title="Claude Code Proxy",
    description="Anthropic API-compatible server powered by Claude Code CLI",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(analytics_router)
app.include_router(benchmark_router)


@app.get("/")
async def health():
    return {"status": "ok", "claude_binary": settings.get_claude_path()}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
