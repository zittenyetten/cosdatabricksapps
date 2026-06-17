import json
import re
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

from .prompts import (
    CHAT_SYSTEM_PROMPT,
    INTENT_SYSTEM,
    INTENT_USER,
    LLM_PARAMS,
    POSTCHECK_SYSTEM,
    POSTCHECK_USER,
    PROMPT_SQL_GENERATION,
    PROMPT_SQL_USER,
    PROMPT_SUMMARIZE_SYSTEM,
    PROMPT_SUMMARIZE_USER,
)
from .settings import RagSettings


class ConversationMemory:
    def __init__(self, max_turns: int = 10):
        self._history = deque(maxlen=max_turns * 2)

    def add(self, role: str, content: str) -> None:
        self._history.append({"role": role, "content": content})

    def get_messages(self) -> list[dict[str, str]]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()
        print("[MEMORY] 대화 이력 초기화됨")

    def __len__(self) -> int:
        return len(self._history) // 2


class DatabricksLLM:
    def __init__(self, settings: RagSettings, workspace_client: WorkspaceClient | None = None):
        self.settings = settings
        self.w = workspace_client or WorkspaceClient()

    def llm_call(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> str:
        resp = self.w.serving_endpoints.query(
            name=self.settings.llm_model,
            messages=[
                ChatMessage(role=ChatMessageRole.SYSTEM, content=system),
                ChatMessage(role=ChatMessageRole.USER, content=user),
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content

    @staticmethod
    def extract_sql(text: str) -> str:
        fenced = re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.IGNORECASE | re.DOTALL)
        if fenced:
            return fenced.group(1).strip()

        stripped = text.strip()
        sql_start = re.search(r"(?is)\b(?:with|select)\b.*", stripped)
        return sql_start.group(0).strip() if sql_start else stripped

    def generate_sql(
        self,
        question: str,
        context: str,
        table_list: str,
        *,
        table_id_mapping: str = "",
        error_msg: str | None = None,
    ) -> str:
        error_section = f"\\n\\n## PREVIOUS ERROR:\\n{error_msg}" if error_msg else ""
        system = PROMPT_SQL_GENERATION.format(
            catalog=self.settings.catalog,
            table_list=table_list,
            table_id_mapping=table_id_mapping or "(Allowed Tables에서 선택)",
            error_section=error_section,
        )
        return self.llm_call(
            system,
            PROMPT_SQL_USER.format(context=context, question=question),
            **LLM_PARAMS["sql_generation"],
        )

    def summarize_results(self, question: str, sql: str, results_str: str) -> str:
        return self.llm_call(
            PROMPT_SUMMARIZE_SYSTEM,
            PROMPT_SUMMARIZE_USER.format(question=question, sql=sql, results=results_str),
            **LLM_PARAMS["summarization"],
        )

    def post_check(self, role: str, allowed_tables: str, sql: str, results_str: str) -> str:
        return self.llm_call(
            POSTCHECK_SYSTEM,
            POSTCHECK_USER.format(
                role=role,
                allowed_tables=allowed_tables,
                sql=sql,
                results=results_str,
            ),
            max_tokens=128,
            temperature=0.0,
        ).strip()

    def search_metadata(
        self,
        query: str,
        *,
        top_k: int,
        vs_index_name: str,
        allowed_domains: list[str] | None = None,
    ) -> list[list[str]]:
        kwargs: dict[str, object] = {
            "index_name": vs_index_name,
            "query_text": query,
            "num_results": top_k,
            "columns": ["source_type", "source_id", "chunk_text", "domain"],
        }
        if allowed_domains:
            kwargs["filters_json"] = json.dumps({"domain": allowed_domains})
        return self.w.vector_search_indexes.query_index(**kwargs).result.data_array

    @staticmethod
    def build_context(results: list[list[str]]) -> str:
        return "\\n\\n".join(
            f"--- [{row[0]}] {row[1]} ({row[3]}) ---\\n{row[2]}" for row in results
        )

    def classify_intent(self, question: str) -> str:
        result = self.llm_call(
            INTENT_SYSTEM,
            INTENT_USER.format(question=question),
            max_tokens=10,
            temperature=0.0,
        ).strip().upper()
        return "WORK" if "WORK" in result else "CHAT"

    def handle_chat(self, question: str, memory: ConversationMemory) -> dict[str, object]:
        messages = [ChatMessage(role=ChatMessageRole.SYSTEM, content=CHAT_SYSTEM_PROMPT)]
        for msg in memory.get_messages():
            role = ChatMessageRole.USER if msg["role"] == "user" else ChatMessageRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=msg["content"]))
        messages.append(ChatMessage(role=ChatMessageRole.USER, content=question))

        resp = self.w.serving_endpoints.query(
            name=self.settings.llm_model,
            messages=messages,
            max_tokens=1024,
            temperature=0.3,
        )
        answer = resp.choices[0].message.content

        memory.add("user", question)
        memory.add("assistant", answer)

        return {
            "request_id": f"chat-{datetime.now(ZoneInfo('Asia/Seoul')).timestamp()}",
            "query_time": datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None),
            "mode": "CHAT",
            "status": "SUCCESS",
            "execution_status": "SUCCESS",
            "question": question,
            "answer": answer,
            "summary": answer,
            "detail": None,
            "conversation_turns": len(memory),
        }
