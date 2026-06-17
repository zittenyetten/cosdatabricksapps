import base64
import json
from typing import Any

from .app import RagApp


def decode_question(dbutils: Any) -> str:
    question_b64 = dbutils.widgets.get("question_b64")
    question_encoding = dbutils.widgets.get("question_encoding")
    if question_b64 and question_encoding == "base64_utf8":
        return base64.b64decode(question_b64).decode("utf-8")
    return dbutils.widgets.get("question")


def run_and_format_response(app: RagApp, dbutils: Any) -> dict[str, Any]:
    question = decode_question(dbutils)
    role_id = dbutils.widgets.get("role_id")

    print(f"FINAL question = {question}")
    print(f"FINAL role_id  = {role_id}")

    result = app.router.route_query(question, role_id=role_id, verbose=False)

    mode = result.get("mode", "WORK")
    status = result.get("status", "UNKNOWN")
    blocked = status in ["DENIED", "BLOCKED"]

    print(f"ROUTE  mode     = {mode}")
    print(f"ROUTE  status   = {status}")

    if mode == "CHAT":
        return {
            "request_id": result.get("request_id"),
            "mode": "CHAT",
            "answer": result.get("answer", ""),
            "guard_status": "PASS",
            "answer_guard_status": "PASS",
            "blocked": False,
            "conversation_turns": result.get("conversation_turns", 0),
            "sources": {"tables": [], "documents": []},
            "checks": {
                "rbac_enabled": False,
                "pre_check": "SKIPPED",
                "post_check": "SKIPPED",
            },
        }

    if mode == "SYSTEM":
        return {
            "mode": "SYSTEM",
            "answer": result.get("answer", ""),
            "guard_status": "PASS",
            "answer_guard_status": "PASS",
            "blocked": False,
        }

    table_access = result.get("table_access", [])
    allowed_tables = [
        str(item.get("table"))
        for item in table_access
        if isinstance(item, dict) and item.get("result") not in ["DENIED", "BLOCKED"]
    ]

    return {
        "request_id": result.get("request_id"),
        "mode": "WORK",
        "answer": result.get("summary") or result.get("detail") or "",
        "guard_status": status,
        "answer_guard_status": "PASS" if not blocked else "BLOCKED",
        "blocked": blocked,
        "sources": {
            "tables": [] if blocked else allowed_tables,
            "documents": [],
        },
        "checks": {
            "rbac_enabled": bool(result.get("rbac_enabled", True)),
            "pre_check": "BLOCKED" if status == "DENIED" else "PASS",
            "post_check": "PASS" if result.get("post_check", True) else "SKIPPED",
        },
        "raw": result,
    }


def run_and_exit_notebook(app: RagApp, dbutils: Any) -> None:
    response = run_and_format_response(app, dbutils)
    dbutils.notebook.exit(json.dumps(response, ensure_ascii=False, default=str))