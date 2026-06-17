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
TABLE_ALIAS_PATTERN = re.compile(
    r"(?i)\b(?:from|join)\s+([`A-Za-z0-9_.]+)(?:\s+(?:as\s+)?([A-Za-z_][A-Za-z0-9_]*))?"
)
LIMIT_PATTERN = re.compile(r"(?is)\blimit\s+(\d+)\s*$")
STRING_PATTERN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
QUALIFIED_COLUMN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
FUNCTION_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

CLAUSE_KEYWORDS = {
    "where",
    "join",
    "inner",
    "left",
    "right",
    "full",
    "cross",
    "on",
    "group",
    "order",
    "having",
    "limit",
    "qualify",
    "union",
}
SQL_KEYWORDS = {
    "all",
    "and",
    "as",
    "asc",
    "between",
    "by",
    "case",
    "cast",
    "count",
    "current_date",
    "date",
    "day",
    "desc",
    "distinct",
    "else",
    "end",
    "false",
    "from",
    "group",
    "having",
    "in",
    "inner",
    "is",
    "join",
    "left",
    "like",
    "limit",
    "lower",
    "max",
    "min",
    "month",
    "not",
    "null",
    "on",
    "or",
    "order",
    "right",
    "round",
    "select",
    "sum",
    "then",
    "true",
    "upper",
    "when",
    "where",
    "year",
}


def normalize_table_name(table: str) -> str:
    return table.replace("`", "").strip().lower()


def extract_sql_tables(sql: str) -> list[str]:
    return sorted({normalize_table_name(match) for match in TABLE_PATTERN.findall(sql)})


def validate_select_sql(
    sql: str,
    allowed_tables: set[str],
    *,
    table_columns: dict[str, list[str]] | None = None,
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

    if table_columns:
        _validate_columns(normalized, tables, table_columns)

    limit_match = LIMIT_PATTERN.search(normalized)
    if limit_match:
        requested_limit = int(limit_match.group(1))
        if requested_limit > max_rows:
            normalized = normalized[: limit_match.start()].rstrip() + f" LIMIT {max_rows}"
    else:
        normalized = f"{normalized} LIMIT {max_rows}"

    return SqlValidationResult(sql=normalized, tables=tables)


def _validate_columns(
    sql: str,
    tables: list[str],
    table_columns: dict[str, list[str]],
) -> None:
    if sql.lstrip().lower().startswith("with "):
        return

    normalized_column_map = {
        normalize_table_name(table): {column.lower() for column in columns}
        for table, columns in table_columns.items()
        if columns
    }
    referenced_columns = {
        table: normalized_column_map[table]
        for table in tables
        if table in normalized_column_map
    }
    if not referenced_columns:
        return

    alias_to_table = _extract_table_aliases(sql, tables)
    sanitized = STRING_PATTERN.sub(" ", sql)
    known_columns = set().union(*referenced_columns.values())
    invalid: list[str] = []

    for qualifier, column in QUALIFIED_COLUMN_PATTERN.findall(sanitized):
        table = alias_to_table.get(qualifier.lower())
        if table and column.lower() not in referenced_columns.get(table, set()):
            invalid.append(f"{qualifier}.{column}")

    sanitized = QUALIFIED_COLUMN_PATTERN.sub(" ", sanitized)
    for table in tables:
        sanitized = re.sub(re.escape(table), " ", sanitized, flags=re.IGNORECASE)

    function_names = {match.lower() for match in FUNCTION_PATTERN.findall(sanitized)}
    output_aliases = {
        match.lower()
        for match in re.findall(r"(?i)\bas\s+([A-Za-z_][A-Za-z0-9_]*)\b", sanitized)
    }
    ignored = (
        SQL_KEYWORDS
        | CLAUSE_KEYWORDS
        | function_names
        | output_aliases
        | set(alias_to_table)
        | _table_name_parts(tables)
    )

    for token in IDENTIFIER_PATTERN.findall(sanitized):
        lowered = token.lower()
        if lowered in ignored:
            continue
        if lowered not in known_columns:
            invalid.append(token)

    if invalid:
        available = ", ".join(sorted(known_columns))
        invalid_list = ", ".join(sorted(set(invalid)))
        raise SqlValidationError(
            f"SQL references unavailable columns: {invalid_list}. "
            f"Use only these columns for referenced tables: {available}"
        )


def _extract_table_aliases(sql: str, tables: list[str]) -> dict[str, str]:
    referenced = set(tables)
    aliases: dict[str, str] = {}
    for table, alias in TABLE_ALIAS_PATTERN.findall(sql):
        normalized_table = normalize_table_name(table)
        if normalized_table not in referenced:
            continue
        table_parts = normalized_table.split(".")
        aliases[table_parts[-1]] = normalized_table
        aliases[normalized_table] = normalized_table
        if alias and alias.lower() not in CLAUSE_KEYWORDS:
            aliases[alias.lower()] = normalized_table
    return aliases


def _table_name_parts(tables: list[str]) -> set[str]:
    parts: set[str] = set()
    for table in tables:
        parts.update(part.lower() for part in table.split("."))
    return parts
