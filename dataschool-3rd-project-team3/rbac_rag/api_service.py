from datetime import date, datetime
from typing import Any

from .audit import SqlAuditLogger
from .engine import EventCallback, RagEngine
from .llm import DatabricksLLM
from .mappings import TableMappings
from .rbac import list_role_ids, validate_role_id
from .router import QueryRouter
from .settings import RagSettings
from .sql_client import DatabricksSqlClient


class RagApiService:
    def __init__(
        self,
        *,
        settings: RagSettings | None = None,
        sql_client: Any | None = None,
        llm: Any | None = None,
        audit_logger: Any | None = None,
    ):
        self.settings = settings or RagSettings.from_env()
        self.sql_client = sql_client or DatabricksSqlClient()
        self.role_ids = list_role_ids(self.sql_client, self.settings.catalog)
        if not self.role_ids:
            raise ValueError(f"No roles found in {self.settings.catalog}.silver.roles")
        self.mappings = TableMappings.build(self.sql_client, self.settings.catalog)
        self.llm = llm or DatabricksLLM(self.settings)
        selected_role = "GENERAL_EMPLOYEE" if "GENERAL_EMPLOYEE" in self.role_ids else self.role_ids[0]
        logger = audit_logger or SqlAuditLogger(self.sql_client, self.settings.log_table).save
        self.rag_engine = RagEngine(
            spark=self.sql_client,
            llm=self.llm,
            settings=self.settings,
            mappings=self.mappings,
            selected_role=selected_role,
            rbac_enabled=True,
            post_check_enabled=True,
            allowed_domains=[],
            valid_role_ids=self.role_ids,
            audit_logger=logger,
            display_results=False,
        )
        self.router = QueryRouter(rag_engine=self.rag_engine, llm=self.llm)

    def chat(
        self,
        *,
        question: str,
        role_id: str,
        mode: str = "auto",
        rbac_enabled: bool = True,
        post_check: bool = True,
        top_k: int | None = None,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        role_id = validate_role_id(role_id, self.role_ids)
        if event_callback is not None:
            event_callback("accepted", {"role_id": role_id, "mode": mode})
        result = self.router.route_query(
            question,
            role_id=role_id,
            mode=mode,
            top_k=top_k,
            rbac_enabled=rbac_enabled,
            post_check_enabled=post_check,
            event_callback=event_callback,
            verbose=False,
        )
        return format_api_response(result, requested_role_id=role_id)


def format_api_response(result: dict[str, Any], *, requested_role_id: str | None = None) -> dict[str, Any]:
    mode = result.get("mode", "WORK")
    status = result.get("status", "UNKNOWN")
    blocked = status in {"DENIED", "BLOCKED"} or result.get("execution_status") == "BLOCKED"
    rows = _extract_rows(result.get("data"))
    table_access = result.get("table_access") or []
    source_tables = [
        str(item.get("table"))
        for item in table_access
        if isinstance(item, dict) and item.get("result") not in {"DENIED", "BLOCKED"}
    ]

    if mode == "CHAT":
        answer = result.get("answer") or result.get("summary") or ""
    else:
        answer = result.get("summary") or result.get("detail") or result.get("answer") or ""

    response = {
        "request_id": result.get("request_id"),
        "mode": mode,
        "status": status,
        "answer": answer,
        "blocked": blocked,
        "role_id": result.get("role") or requested_role_id,
        "generated_sql": result.get("sql"),
        "columns": result.get("columns_returned") or _columns_from_rows(rows),
        "rows": [] if blocked else rows,
        "row_count": result.get("row_count_returned") if result.get("row_count_returned") is not None else len(rows),
        "sources": {
            "tables": [] if blocked else source_tables,
            "documents": [],
        },
        "checks": {
            "rbac_enabled": bool(result.get("rbac_enabled", mode == "WORK")),
            "pre_check": "BLOCKED" if result.get("failure_reason") in {"RBAC_DOMAIN_DENIED", "NO_SEARCH_RESULT"} else "PASS",
            "post_check": _post_check_status(result),
        },
    }
    response["raw"] = _jsonable({**result, "data": rows})
    return response


def _post_check_status(result: dict[str, Any]) -> str:
    if not result.get("post_check"):
        return "SKIPPED"
    if result.get("failure_reason") == "POST_CHECK_FAILED":
        return "BLOCKED"
    return "PASS"


def _extract_rows(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [dict(row) if isinstance(row, dict) else {"value": row} for row in data]
    try:
        table = data.limit(20).toPandas()
        return table.to_dict(orient="records")
    except Exception:
        return []


def _columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    return list(rows[0].keys())


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    try:
        import uuid

        if isinstance(value, uuid.UUID):
            return str(value)
    except Exception:
        pass
    return value
