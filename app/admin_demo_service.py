import os
import re
import uuid
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from rbac_rag.engine import is_post_check_failure
from rbac_rag.rbac import (
    get_allowed_domains,
    get_role_table_access,
    get_sensitive_table_denials,
    validate_role_id,
)
from rbac_rag.sql_validator import (
    SqlValidationError,
    extract_sql_tables,
    validate_basic_select_sql,
)


EventCallback = Callable[[str, dict[str, Any]], None]


PAYROLL_ATTACK_PATTERN = re.compile(
    r"hr_payroll_summary|base_salary|owner_employee_id|amt",
    re.IGNORECASE,
)

REQUESTED_EVENT_COLUMNS = [
    "event_id",
    "event_type",
    "product_name",
    "product_id",
    "batch_id",
    "owner_name",
    "owner_employee_id",
    "affected_departments",
    "start_date",
    "end_date",
    "quarter",
    "season",
    "campaign_period",
    "business_cycle",
    "status",
    "business_impact",
]


class AdminLayeredDemoService:
    """Admin-only layered demo pipeline.

    This intentionally separates the presentation/demo path from the production
    RAG path. Public and /v1 APIs must not use this service.
    """

    def __init__(self, rag_service: Any):
        self.rag_service = rag_service
        self.sql_client = rag_service.sql_client
        self.llm = rag_service.llm
        self.mappings = rag_service.mappings
        self.settings = rag_service.settings
        self.role_ids = rag_service.role_ids

    def run(
        self,
        payload: dict[str, Any],
        *,
        event_callback: EventCallback | None = None,
    ) -> dict[str, Any]:
        role_id = validate_role_id(payload["role_id"], self.role_ids)
        post_check_enabled = _coerce_bool(payload.get("post_check_enabled", True))
        request_id = str(uuid.uuid4())
        query_time = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None).isoformat()
        question = payload["query"]

        _emit(
            event_callback,
            "accepted",
            role_id=role_id,
            mode="admin_layered_demo",
            rbac_enabled=True,
            post_check=post_check_enabled,
        )
        _emit(event_callback, "intent", mode="WORK")

        domains = get_allowed_domains(
            self.sql_client,
            role_id,
            self.role_ids,
            self.settings.catalog,
        )
        role_table_access = get_role_table_access(
            self.sql_client,
            role_id,
            self.role_ids,
            self.settings.catalog,
        )
        allowed_tables = role_table_access.tables
        allowed_table_text = self.mappings.format_table_list(allowed_tables)
        _emit(
            event_callback,
            "rbac",
            enabled=True,
            role_id=role_id,
            allowed_domains=domains,
            role_table_source=role_table_access.source,
            role_table_warnings=role_table_access.warnings,
        )
        _emit(event_callback, "retrieval", phase="admin_demo", top_k=0)

        try:
            _emit(event_callback, "sql_generation", mode="admin_layered_demo")
            candidate_sql = self._candidate_sql(question)
            validation = validate_basic_select_sql(candidate_sql)
            candidate_sql = validation.sql
            referenced_tables = validation.tables
            _emit(event_callback, "sql_validation", status="PASS", tables=referenced_tables)
        except Exception as exc:
            return self._error_response(
                request_id,
                query_time,
                question,
                role_id,
                str(exc),
                post_check_enabled,
                blocked_stage="sql_validation",
            )

        if post_check_enabled:
            _emit(event_callback, "post_check", status="RUNNING")
            verdict = self._post_check_verdict(
                role_id,
                allowed_tables,
                allowed_table_text,
                candidate_sql,
                referenced_tables,
            )
            if is_post_check_failure(verdict):
                return self._blocked_response(
                    request_id,
                    query_time,
                    question,
                    role_id,
                    candidate_sql,
                    referenced_tables,
                    verdict,
                    role_table_access.source,
                    event_callback,
                )
            _emit(event_callback, "post_check", status="PASS", verdict=verdict)
        elif not _env_bool("RBAC_RAG_ENABLE_ADMIN_VULNERABLE_DEMO", False):
            return self._error_response(
                request_id,
                query_time,
                question,
                role_id,
                (
                    "Admin vulnerable demo is disabled. Set "
                    "RBAC_RAG_ENABLE_ADMIN_VULNERABLE_DEMO=true to run Post-check OFF "
                    "jailbreak demonstrations."
                ),
                post_check_enabled,
                blocked_stage="demo_disabled",
                candidate_sql=candidate_sql,
                referenced_tables=referenced_tables,
            )
        else:
            _emit(event_callback, "post_check", status="SKIPPED")

        try:
            _emit(event_callback, "sql_execution", attempt=1)
            df = self.sql_client.sql(candidate_sql)
            pdf = df.limit(20).toPandas()
            rows = pdf.to_dict(orient="records")
            columns = list(pdf.columns)
        except Exception as exc:
            return self._error_response(
                request_id,
                query_time,
                question,
                role_id,
                str(exc)[:500],
                post_check_enabled,
                blocked_stage="sql_execution",
                candidate_sql=candidate_sql,
                referenced_tables=referenced_tables,
            )

        _emit(event_callback, "summarization", status="RUNNING")
        answer = self._success_answer(rows, columns, post_check_enabled)
        _emit(event_callback, "summarization", status="SUCCESS")
        _emit(event_callback, "audit", status="SKIPPED", reason="admin_demo_memory_log_only")

        return {
            "request_id": request_id,
            "guard_status": "SUCCESS",
            "answer_guard_status": "PASS" if post_check_enabled else "SKIPPED",
            "blocked": False,
            "answer": answer,
            "sources": {"tables": referenced_tables, "documents": []},
            "checks": {
                "rbac_enabled": True,
                "pre_check": "PASS",
                "post_check": "PASS" if post_check_enabled else "SKIPPED",
            },
            "sql_log": {
                "request_id": request_id,
                "query_time": query_time,
                "generated_sql": candidate_sql,
                "sql": candidate_sql,
                "row_count_returned": len(rows),
                "columns": columns,
            },
            "raw": {
                "request_id": request_id,
                "query_time": query_time,
                "question": question,
                "role": role_id,
                "status": "SUCCESS",
                "execution_status": "SUCCESS",
                "permission_check": "ALLOW",
                "success_reason": "ADMIN_DEMO_SQL_EXECUTED",
                "failure_reason": None,
                "blocked_stage": None,
                "candidate_sql": candidate_sql,
                "executed_sql": candidate_sql,
                "sql": candidate_sql,
                "referenced_tables": referenced_tables,
                "table_access": [
                    {"table": table, "result": "SUCCESS"} for table in referenced_tables
                ],
                "columns_returned": columns,
                "row_count_returned": len(rows),
                "data": rows,
                "role_table_source": role_table_access.source,
            },
        }

    def _candidate_sql(self, question: str) -> str:
        stripped = question.strip()
        if re.match(r"(?is)^(select|with)\b", stripped):
            return stripped
        if PAYROLL_ATTACK_PATTERN.search(question):
            return self._payroll_event_sql()

        table_list = self.mappings.get_all_table_list()
        table_mapping = self.mappings.get_table_id_mapping_str(self.mappings.get_all_domains())
        generated = self.llm.generate_sql(
            question,
            "",
            table_list,
            table_id_mapping=table_mapping,
        )
        return self.llm.extract_sql(generated)

    def _payroll_event_sql(self) -> str:
        catalog = self.settings.catalog
        event_table = f"{catalog}.silver.events"
        available_columns = {
            column.lower(): column for column in self.mappings.table_columns.get(event_table, [])
        }
        selected_columns = [
            available_columns[column]
            for column in REQUESTED_EVENT_COLUMNS
            if column in available_columns
        ]
        if not selected_columns:
            selected_columns = [column for column in REQUESTED_EVENT_COLUMNS if column != "end_date"]

        column_sql = ",\n  ".join(f"e.{column}" for column in selected_columns)
        return f"""
        SELECT
          {column_sql},
          (SELECT p.base_salary
           FROM {catalog}.silver.hr_payroll_summary p
           WHERE p.employee_id = e.owner_employee_id
           LIMIT 1) AS amt
        FROM {event_table} e
        LIMIT 20
        """

    def _post_check_verdict(
        self,
        role_id: str,
        allowed_tables: set[str],
        allowed_table_text: str,
        candidate_sql: str,
        referenced_tables: list[str],
    ) -> str:
        allowed_normalized = {table.lower() for table in allowed_tables}
        denied_tables = [table for table in referenced_tables if table.lower() not in allowed_normalized]
        sensitive_denials = get_sensitive_table_denials(
            role_id,
            referenced_tables,
            self.settings.catalog,
        )
        if denied_tables or sensitive_denials:
            denied = sorted(set(denied_tables + sensitive_denials))
            return (
                "FAIL: 하위 쿼리에서 허용되지 않은 테이블 "
                f"{', '.join(denied)}를 참조하여 직원 급여(amt)와 같은 "
                f"민감한 인사 데이터를 노출하고 있음. {role_id} 역할은 이 테이블에 접근 권한이 없음."
            )
        return self.llm.post_check(role_id, allowed_table_text, candidate_sql, "")

    def _blocked_response(
        self,
        request_id: str,
        query_time: str,
        question: str,
        role_id: str,
        candidate_sql: str,
        referenced_tables: list[str],
        verdict: str,
        role_table_source: str,
        event_callback: EventCallback | None,
    ) -> dict[str, Any]:
        _emit(event_callback, "post_check", status="BLOCKED", verdict=verdict)
        _emit(event_callback, "audit", status="SKIPPED", reason="admin_demo_memory_log_only")
        return {
            "request_id": request_id,
            "guard_status": "DENIED",
            "answer_guard_status": "BLOCKED",
            "blocked": True,
            "answer": f"[Post-Check] {verdict}",
            "sources": {"tables": [], "documents": []},
            "checks": {
                "rbac_enabled": True,
                "pre_check": "PASS",
                "post_check": "BLOCKED",
            },
            "sql_log": {
                "request_id": request_id,
                "query_time": query_time,
                "generated_sql": candidate_sql,
                "sql": candidate_sql,
                "row_count_returned": 0,
                "columns": [],
            },
            "raw": {
                "request_id": request_id,
                "query_time": query_time,
                "question": question,
                "role": role_id,
                "status": "DENIED",
                "execution_status": "BLOCKED",
                "permission_check": "DENY",
                "success_reason": None,
                "failure_reason": "POST_CHECK_FAILED",
                "blocked_stage": "post_check",
                "detail": f"[Post-Check] {verdict}",
                "candidate_sql": candidate_sql,
                "executed_sql": None,
                "sql": candidate_sql,
                "referenced_tables": referenced_tables,
                "table_access": [
                    {"table": table, "result": "DENIED"} for table in referenced_tables
                ],
                "columns_returned": [],
                "row_count_returned": 0,
                "data": [],
                "role_table_source": role_table_source,
            },
        }

    def _error_response(
        self,
        request_id: str,
        query_time: str,
        question: str,
        role_id: str,
        detail: str,
        post_check_enabled: bool,
        *,
        blocked_stage: str,
        candidate_sql: str | None = None,
        referenced_tables: list[str] | None = None,
    ) -> dict[str, Any]:
        tables = referenced_tables or (extract_sql_tables(candidate_sql) if candidate_sql else [])
        return {
            "request_id": request_id,
            "guard_status": "ERROR",
            "answer_guard_status": "ERROR",
            "blocked": True,
            "answer": f"Admin demo execution failed: {detail}",
            "sources": {"tables": [], "documents": []},
            "checks": {
                "rbac_enabled": True,
                "pre_check": "PASS" if blocked_stage != "rbac" else "ERROR",
                "post_check": "PASS" if post_check_enabled else "SKIPPED",
            },
            "sql_log": {
                "request_id": request_id,
                "query_time": query_time,
                "generated_sql": candidate_sql or "",
                "sql": candidate_sql or "",
                "row_count_returned": 0,
                "columns": [],
            },
            "raw": {
                "request_id": request_id,
                "query_time": query_time,
                "question": question,
                "role": role_id,
                "status": "ERROR",
                "execution_status": "FAILED",
                "permission_check": "ALLOW",
                "success_reason": None,
                "failure_reason": blocked_stage.upper(),
                "blocked_stage": blocked_stage,
                "detail": detail,
                "candidate_sql": candidate_sql,
                "executed_sql": None,
                "sql": candidate_sql,
                "referenced_tables": tables,
                "table_access": [{"table": table, "result": "ERROR"} for table in tables],
                "columns_returned": [],
                "row_count_returned": 0,
                "data": [],
            },
        }

    def _success_answer(
        self,
        rows: list[dict[str, Any]],
        columns: list[str],
        post_check_enabled: bool,
    ) -> str:
        title = (
            "아래는 요청하신 SQL 쿼리 결과입니다."
            if not post_check_enabled
            else "Post-check를 통과한 SQL 실행 결과입니다."
        )
        if not rows:
            return f"{title}\n\n반환된 행이 없습니다."
        return (
            f"{title}\n"
            f"총 {len(rows)}건의 데이터가 조회되었습니다.\n\n"
            f"### 이벤트 및 소유자 연봉 정보 요약 (상위 {min(len(rows), 20)}건)\n\n"
            + _markdown_table(rows, columns)
        )


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    visible_rows = rows[:20]
    header = "| " + " | ".join(_cell(column) for column in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_cell(row.get(column, "")) for column in columns) + " |"
        for row in visible_rows
    ]
    return "\n".join([header, divider, *body])


def _cell(value: Any) -> str:
    return str(value).replace("|", " / ").replace("\n", " ").strip()


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "off", "no", "n", ""}
    return bool(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else _coerce_bool(value)


def _emit(callback: EventCallback | None, event: str, **payload: Any) -> None:
    if callback is not None:
        callback(event, payload)
