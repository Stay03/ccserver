from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models.request import MessagesRequest
from app.models.response import ErrorResponse, ErrorDetail
from app.services.claude_cli import run_claude, stream_claude

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_api_key(api_key: str | None) -> None:
    if settings.api_key and api_key != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail={"type": "error", "error": {"type": "authentication_error", "message": "Invalid API key"}},
        )


@router.post("/v1/messages")
async def create_message(
    request: MessagesRequest,
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
):
    key = x_api_key
    if not key and authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    _check_api_key(key)

    if request.stream:
        return StreamingResponse(
            stream_claude(request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        response = await run_claude(request)
        return JSONResponse(content=response.model_dump())
    except RuntimeError as e:
        error_msg = str(e)
        logger.error("Claude CLI error: %s", error_msg)
        if "not found" in error_msg.lower():
            status = 503
            error_type = "api_error"
        elif "timed out" in error_msg.lower():
            status = 408
            error_type = "overloaded_error"
        elif "authentication" in error_msg.lower() or "logged in" in error_msg.lower() or "login" in error_msg.lower():
            status = 401
            error_type = "authentication_error"
        else:
            status = 500
            error_type = "api_error"

        return JSONResponse(
            status_code=status,
            content=ErrorResponse(
                error=ErrorDetail(type=error_type, message=error_msg)
            ).model_dump(),
        )
