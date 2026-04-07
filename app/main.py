import logging

import uvicorn
from fastapi import FastAPI

from app.config import settings
from app.routes.messages import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Claude Code Proxy",
    description="Anthropic API-compatible server powered by Claude Code CLI",
    version="0.1.0",
)

app.include_router(router)


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
