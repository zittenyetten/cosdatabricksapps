import re
from dataclasses import dataclass
from typing import Collection

from .rbac import SENSITIVE_TABLE_ROLE_ALLOWLIST, get_sensitive_table_denials
from .sql_validator import extract_sql_tables, normalize_table_name


SQL_SNIPPET_PATTERN = re.compile(r"(?is)(```sql|\bselect\b.+\bfrom\b)")
FQN_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b")
IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
AS_ALIAS_PATTERN = re.compile(r"(?i)\bas\s+([A-Za-z_][A-Za-z0-9_]*)\b")
LARGE_NUMBER_PATTERN = re.compile(r"(?<![\w-])\d{5,}(?![\w-])")


@dataclass(frozen=True)
class AnswerGuardResult:
    allowed: bool
    reasons: list[str]


def validate_answer_summary(
    summary: str,
    *,
    role_id: str | None,
    catalog: str,
    executed_sql: str,
    referenced_tables: Collection[str],
    returned_columns: Collection[str],
    results_text: str,
) -> AnswerGuardResult:
    """Validate that an LLM summary does not invent data outside executed results."""
    text = summary or ""
    if not text.strip():
        return AnswerGuardResult(allowed=True, reasons=[])

    reasons: list[str] = []
    referenced = {normalize_table_name(table) for table in referenced_tables}
    mentioned_tables = _mentioned_tables(text)
    non_executed_tables = sorted(table for table in mentioned_tables if table not in referenced)
    if non_executed_tables:
        reasons.append("Answer references non-executed tables: " + ", ".join(non_executed_tables))

    sensitive_mentions = _sensitive_table_mentions(text, catalog)
    sensitive_denials = get_sensitive_table_denials(role_id, sensitive_mentions, catalog)
    if sensitive_denials:
        reasons.append(
            "Answer references sensitive tables not allowed for role "
            f"{role_id}: {', '.join(sensitive_denials)}"
        )

    if SQL_SNIPPET_PATTERN.search(text):
        reasons.append("Answer includes SQL text; only executed-result summaries are allowed")

    unknown_identifiers = _unknown_identifiers(text, returned_columns)
    if unknown_identifiers:
        reasons.append(
            "Answer references columns or aliases not returned by SQL: "
            + ", ".join(unknown_identifiers)
        )

    fabricated_numbers = _fabricated_numbers(text, executed_sql, results_text)
    if fabricated_numbers:
        reasons.append(
            "Answer contains numeric values not present in executed SQL results: "
            + ", ".join(fabricated_numbers[:10])
        )

    return AnswerGuardResult(allowed=not reasons, reasons=reasons)


def _mentioned_tables(text: str) -> set[str]:
    tables = {normalize_table_name(table) for table in extract_sql_tables(text)}
    tables.update(normalize_table_name(match) for match in FQN_PATTERN.findall(text))
    return tables


def _sensitive_table_mentions(text: str, catalog: str) -> list[str]:
    lowered = text.lower()
    mentions: list[str] = []
    for suffix in SENSITIVE_TABLE_ROLE_ALLOWLIST:
        suffix_lower = suffix.lower()
        table_name = suffix_lower.rsplit(".", 1)[-1]
        fqn = f"{catalog}.{suffix_lower}"
        if suffix_lower in lowered or table_name in lowered or fqn in lowered:
            mentions.append(f"{catalog}.{suffix}")
    return mentions


def _unknown_identifiers(text: str, returned_columns: Collection[str]) -> list[str]:
    returned = {column.lower() for column in returned_columns}
    if not returned:
        return []

    aliases = {alias.lower() for alias in AS_ALIAS_PATTERN.findall(text)}
    code_identifiers = {
        token.lower()
        for token in IDENTIFIER_PATTERN.findall(text)
        if "_" in token or token.lower() in aliases
    }
    ignored = {
        "cos_adb",
        "sql",
        "select",
        "from",
        "where",
        "limit",
        "null",
    }
    unknown = sorted(token for token in code_identifiers if token not in returned and token not in ignored)
    return unknown


def _fabricated_numbers(text: str, executed_sql: str, results_text: str) -> list[str]:
    allowed_text = f"{executed_sql}\n{results_text}"
    numbers = set(LARGE_NUMBER_PATTERN.findall(text))
    return sorted(number for number in numbers if number not in allowed_text)
