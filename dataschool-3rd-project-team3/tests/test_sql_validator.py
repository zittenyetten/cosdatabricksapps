import pytest

from rbac_rag.sql_validator import SqlValidationError, validate_select_sql


ALLOWED = {"cos_adb.silver.events", "cos_adb.silver.products"}


def test_validate_select_sql_adds_limit() -> None:
    result = validate_select_sql("SELECT * FROM cos_adb.silver.events", ALLOWED)

    assert result.sql.endswith("LIMIT 20")
    assert result.tables == ["cos_adb.silver.events"]


def test_validate_select_sql_reduces_large_limit() -> None:
    result = validate_select_sql("SELECT * FROM cos_adb.silver.events LIMIT 100", ALLOWED)

    assert result.sql.endswith("LIMIT 20")


def test_validate_select_sql_rejects_dml() -> None:
    with pytest.raises(SqlValidationError):
        validate_select_sql("DELETE FROM cos_adb.silver.events", ALLOWED)


def test_validate_select_sql_rejects_multiple_statements() -> None:
    with pytest.raises(SqlValidationError):
        validate_select_sql("SELECT * FROM cos_adb.silver.events; SELECT 1", ALLOWED)


def test_validate_select_sql_rejects_non_allowed_table() -> None:
    with pytest.raises(SqlValidationError):
        validate_select_sql("SELECT * FROM cos_adb.gold.secret_table", ALLOWED)

