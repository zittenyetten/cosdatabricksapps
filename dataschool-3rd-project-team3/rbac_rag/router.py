from typing import Any

from .engine import EventCallback, RagEngine
from .llm import ConversationMemory, DatabricksLLM


class QueryRouter:
    def __init__(self, rag_engine: RagEngine, llm: DatabricksLLM, memory_turns: int = 10):
        self.rag_engine = rag_engine
        self.llm = llm
        self.memory = ConversationMemory(max_turns=memory_turns)

    def route_query(
        self,
        question: str,
        role_id: str | None = None,
        *,
        mode: str = "auto",
        top_k: int | None = None,
        rbac_enabled: bool | None = None,
        post_check_enabled: bool | None = None,
        event_callback: EventCallback | None = None,
        verbose: bool = True,
    ) -> dict[str, Any]:
        raw = question.strip()

        if raw.lower().startswith("/chat"):
            mode = "CHAT"
            clean = raw[5:].strip()
        elif raw.lower().startswith("/work"):
            mode = "WORK"
            clean = raw[5:].strip()
        elif raw.lower() == "/clear":
            self.memory.clear()
            return {
                "mode": "SYSTEM",
                "status": "SUCCESS",
                "answer": "대화 이력이 초기화되었습니다.",
                "summary": "대화 이력이 초기화되었습니다.",
            }
        else:
            normalized_mode = mode.upper()
            mode = self.llm.classify_intent(raw) if normalized_mode == "AUTO" else normalized_mode
            clean = raw

        if not clean:
            return {"mode": mode, "status": "ERROR", "detail": "질문 내용이 비어있습니다."}

        if event_callback is not None:
            event_callback("intent", {"mode": mode})

        if verbose:
            print(f"[ROUTER] mode={mode} | question={clean[:60]}")

        if mode == "CHAT":
            return self.llm.handle_chat(clean, self.memory)

        result = self.rag_engine.ask_rag(
            clean,
            top_k=top_k,
            role_id=role_id,
            rbac_enabled=rbac_enabled,
            post_check_enabled=post_check_enabled,
            event_callback=event_callback,
            verbose=verbose,
        )
        result["mode"] = "WORK"
        return result
