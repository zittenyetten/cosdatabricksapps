import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

try:
    from pyspark.sql.types import ArrayType, LongType, StringType, StructField, StructType
    from pyspark.sql.types import TimestampNTZType
except ImportError:  # FastAPI local runtime does not need PySpark.
    ArrayType = LongType = StringType = StructField = StructType = TimestampNTZType = None


LOG_SCHEMA = (
    StructType(
        [
            StructField("log_id", StringType(), False),
            StructField("request_id", StringType(), True),
            StructField("query_time", TimestampNTZType(), True),
            StructField("user_question", StringType(), True),
            StructField("generated_sql", StringType(), True),
            StructField("tables_accessed", ArrayType(StringType()), True),
            StructField("columns_returned", ArrayType(StringType()), True),
            StructField("row_count_returned", LongType(), True),
            StructField("execution_status", StringType(), True),
            StructField("success_reason", StringType(), True),
            StructField("failure_reason", StringType(), True),
            StructField("error_message", StringType(), True),
            StructField("query_runtime_ms", LongType(), True),
            StructField("user_id", StringType(), True),
            StructField("role_id", StringType(), True),
            StructField("department_id", StringType(), True),
            StructField("permission_check", StringType(), True),
            StructField("created_at", TimestampNTZType(), True),
        ]
    )
    if StructType is not None
    else None
)


def kst_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)


def extract_tables(output: dict[str, Any]) -> list[str]:
    sql = output.get("sql")
    if sql:
        pattern = r"(?i)\b(?:from|join)\s+([`\w\.]+)"
        tables = {
            value.replace("`", "").strip()
            for value in re.findall(pattern, sql)
            if value.replace("`", "").lower().startswith("cos_adb.")
        }
        if tables:
            return sorted(tables)

    return sorted(
        {
            item.get("table")
            for item in (output.get("table_access") or [])
            if isinstance(item, dict) and item.get("table")
        }
    )


def save_rag_log(spark: Any, log_table: str, output: dict[str, Any]) -> str | None:
    try:
        if LOG_SCHEMA is None:
            raise RuntimeError("PySpark is required for notebook audit logging")

        execution_status = output.get("execution_status")

        record = {
            "log_id": str(uuid.uuid4()),
            "request_id": output.get("request_id"),
            "query_time": output.get("query_time"),
            "user_question": output.get("question"),
            "generated_sql": output.get("sql"),
            "tables_accessed": extract_tables(output),
            "columns_returned": output.get("columns_returned") or [],
            "row_count_returned": output.get("row_count_returned"),
            "execution_status": execution_status,
            "success_reason": output.get("success_reason"),
            "failure_reason": output.get("failure_reason"),
            "error_message": output.get("detail") if execution_status == "FAILED" else None,
            "query_runtime_ms": output.get("query_runtime_ms"),
            "user_id": output.get("user_id"),
            "role_id": output.get("role"),
            "department_id": output.get("department_id"),
            "permission_check": output.get("permission_check"),
            "created_at": kst_now(),
        }

        (
            spark.createDataFrame([record], schema=LOG_SCHEMA)
            .write.format("delta")
            .mode("append")
            .saveAsTable(log_table)
        )

        print(f"[LOG SAVED] log_id={record['log_id']}, status={execution_status}")
        return record["log_id"]
    except Exception as error:
        print(f"[LOG ERROR] {str(error)[:500]}")
        return None
