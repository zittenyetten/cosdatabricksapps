import re
from dataclasses import dataclass


class SqlValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SqlValidationResult:
    sql: str
    tables: list[str]


BLOCKED_KEYWORDS = re.compile(
    r"\b("
    r"insert|update|delete|merge|create|drop|alter|copy|call|grant|revoke|"
    r"truncate|use|set|describe|show|explain|refresh|optimize|vacuum"
    r")\b",
    re.IGNORECASE,
)
TABLE_PATTERN = re.compile(r"(?i)\b(?:from|join)\s+([`A-Za-z0-9_.]+)")
LIMIT_PATTERN = re.compile(r"(?is)\blimit\s+(\d+)\s*$")


def normalize_table_name(table: str) -> str:
    return table.replace("`", "").strip().lower()


def extract_sql_tables(sql: str) -> list[str]:
    return sorted({normalize_table_name(match) for match in TABLE_PATTERN.findall(sql)})


def validate_select_sql(
    sql: str,
    allowed_tables: set[str],
    *,
    max_rows: int = 20,
) -> SqlValidationResult:
    normalized = sql.strip().rstrip(";").strip()
    if not normalized:
        raise SqlValidationError("SQL is empty")

    if ";" in normalized:
        raise SqlValidationError("Multiple SQL statements are not allowed")

    first_token = normalized.split(None, 1)[0].lower()
    if first_token not in {"select", "with"}:
        raise SqlValidationError("Only SELECT queries are allowed")

    if BLOCKED_KEYWORDS.search(normalized):
        raise SqlValidationError("Blocked SQL keyword detected")

    tables = extract_sql_tables(normalized)
    if not tables:
        raise SqlValidationError("SQL must reference at least one table")

    allowed_normalized = {normalize_table_name(table) for table in allowed_tables}
    denied_tables = [table for table in tables if table not in allowed_normalized]
    if denied_tables:
        raise SqlValidationError(f"SQL references non-allowed tables: {', '.join(denied_tables)}")

    limit_match = LIMIT_PATTERN.search(normalized)
    if limit_match:
        requested_limit = int(limit_match.group(1))
        if requested_limit > max_rows:
            normalized = normalized[: limit_match.start()].rstrip() + f" LIMIT {max_rows}"
    else:
        normalized = f"{normalized} LIMIT {max_rows}"

    return SqlValidationResult(sql=normalized, tables=tables)

