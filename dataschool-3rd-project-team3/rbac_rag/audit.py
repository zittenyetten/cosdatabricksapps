import json
import uuid
from datetime import datetime
from typing import Any

from .logging_utils import extract_tables, kst_now
from .sql_client import DatabricksSqlClient


class SqlAuditLogger:
    def __init__(self, sql_client: DatabricksSqlClient, log_table: str):
        self.sql_client = sql_client
        self.log_table = log_table

    def save(self, output: dict[str, Any]) -> str | None:
        try:
            log_id = str(uuid.uuid4())
            execution_status = output.get("execution_status")
            query = f"""
            INSERT INTO {self.log_table} (
                log_id,
                request_id,
                query_time,
                user_question,
                generated_sql,
                tables_accessed,
                columns_returned,
                row_count_returned,
                execution_status,
                success_reason,
                failure_reason,
                error_message,
                query_runtime_ms,
                user_id,
                role_id,
                department_id,
                permission_check,
                created_at
            )
            SELECT
                ?,
                ?,
                CAST(? AS TIMESTAMP_NTZ),
                ?,
                ?,
                from_json(?, 'array<string>'),
                from_json(?, 'array<string>'),
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                current_timestamp()
            """
            params = [
                log_id,
                output.get("request_id"),
                _datetime_value(output.get("query_time")),
                output.get("question"),
                output.get("sql"),
                json.dumps(extract_tables(output), ensure_ascii=False),
                json.dumps(output.get("columns_returned") or [], ensure_ascii=False),
                output.get("row_count_returned"),
                execution_status,
                output.get("success_reason"),
                output.get("failure_reason"),
                output.get("detail") if execution_status == "FAILED" else None,
                output.get("query_runtime_ms"),
                output.get("user_id"),
                output.get("role"),
                output.get("department_id"),
                output.get("permission_check"),
            ]
            self.sql_client.sql(query, params)
            return log_id
        except Exception as error:
            print(f"[LOG ERROR] {str(error)[:500]}")
            return None


def _datetime_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return (value or kst_now()).isoformat(sep=" ")

