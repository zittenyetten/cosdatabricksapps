import asyncio
import json
import os
from functools import lru_cache
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from sse_starlette.sse import EventSourceResponse

from rbac_rag.api_service import RagApiService


load_dotenv()

app = FastAPI(title="RBAC RAG API", version="0.1.0")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    role_id: str = Field(..., min_length=1)
    mode: Literal["auto", "chat", "work"] = "auto"
    rbac_enabled: bool = True
    post_check: bool = True
    top_k: int | None = Field(default=None, ge=1, le=20)


@lru_cache(maxsize=1)
def get_service() -> RagApiService:
    return RagApiService()


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "databricks_configured": bool(
            (os.getenv("DATABRICKS_SERVER_HOSTNAME") or os.getenv("DATABRICKS_HOST"))
            and os.getenv("DATABRICKS_HTTP_PATH")
            and os.getenv("DATABRICKS_TOKEN")
        ),
    }


@app.post("/v1/chat")
def chat(request: ChatRequest) -> dict[str, object]:
    try:
        return get_service().chat(
            question=request.question,
            role_id=request.role_id,
            mode=request.mode,
            rbac_enabled=request.rbac_enabled,
            post_check=request.post_check,
            top_k=request.top_k,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=_safe_error(error)) from error


@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest) -> EventSourceResponse:
    async def event_generator():
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: str, payload: dict[str, object]) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "event": event,
                    "data": json.dumps(payload, ensure_ascii=False, default=str),
                },
            )

        def run_chat() -> dict[str, object]:
            return get_service().chat(
                question=request.question,
                role_id=request.role_id,
                mode=request.mode,
                rbac_enabled=request.rbac_enabled,
                post_check=request.post_check,
                top_k=request.top_k,
                event_callback=emit,
            )

        task = asyncio.create_task(asyncio.to_thread(run_chat))
        try:
            while not task.done() or not queue.empty():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
            response = await task
            yield {
                "event": "final",
                "data": json.dumps(response, ensure_ascii=False, default=str),
            }
        except ValueError as error:
            yield {
                "event": "error",
                "data": json.dumps({"status": 400, "detail": str(error)}, ensure_ascii=False),
            }
        except Exception as error:
            yield {
                "event": "error",
                "data": json.dumps({"status": 502, "detail": _safe_error(error)}, ensure_ascii=False),
            }

    return EventSourceResponse(event_generator())


def _safe_error(error: Exception) -> str:
    message = str(error).strip()
    return message[:300] if message else error.__class__.__name__
