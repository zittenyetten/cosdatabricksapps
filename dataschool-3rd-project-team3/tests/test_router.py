from rbac_rag.router import QueryRouter


class FakeEngine:
    def __init__(self) -> None:
        self.calls = []

    def ask_rag(self, question, **kwargs):
        self.calls.append((question, kwargs))
        return {"mode": "WORK", "status": "SUCCESS", "summary": "ok"}


class FakeLLM:
    def classify_intent(self, question):
        return "WORK"

    def handle_chat(self, question, memory):
        return {
            "mode": "CHAT",
            "status": "SUCCESS",
            "answer": f"chat: {question}",
            "conversation_turns": 1,
        }


def test_router_forces_chat_mode() -> None:
    router = QueryRouter(rag_engine=FakeEngine(), llm=FakeLLM())

    result = router.route_query("/chat hello", role_id="GENERAL_EMPLOYEE", verbose=False)

    assert result["mode"] == "CHAT"
    assert result["answer"] == "chat: hello"


def test_router_passes_api_options_to_engine() -> None:
    engine = FakeEngine()
    router = QueryRouter(rag_engine=engine, llm=FakeLLM())

    result = router.route_query(
        "show data",
        role_id="GENERAL_EMPLOYEE",
        mode="work",
        top_k=3,
        rbac_enabled=False,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["mode"] == "WORK"
    question, kwargs = engine.calls[0]
    assert question == "show data"
    assert kwargs["top_k"] == 3
    assert kwargs["rbac_enabled"] is False
    assert kwargs["post_check_enabled"] is False

