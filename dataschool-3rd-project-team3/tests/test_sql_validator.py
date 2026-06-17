import pytest

from rbac_rag.sql_validator import (
    SqlValidationError,
    build_safe_projection_sql,
    validate_basic_select_sql,
    validate_select_sql,
)


ALLOWED = {"cos_adb.silver.events", "cos_adb.silver.products"}
COLUMNS = {
    "cos_adb.silver.events": ["event_id", "status"],
    "cos_adb.silver.products": ["product_id", "product_name", "status"],
}


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


def test_validate_select_sql_rejects_non_allowed_table_in_subquery() -> None:
    with pytest.raises(SqlValidationError, match="hr_payroll_summary"):
        validate_select_sql(
            """
            SELECT e.event_id,
                   (SELECT p.base_salary
                    FROM cos_adb.silver.hr_payroll_summary p
                    WHERE p.employee_id = e.owner_employee_id
                    LIMIT 1) AS amt
            FROM cos_adb.silver.events e
            LIMIT 20
            """,
            {"cos_adb.silver.events"},
        )


def test_validate_basic_select_sql_allows_non_allowed_table_for_admin_demo() -> None:
    result = validate_basic_select_sql(
        """
        SELECT e.event_id,
               (SELECT p.base_salary
                FROM cos_adb.silver.hr_payroll_summary p
                WHERE p.employee_id = e.owner_employee_id
                LIMIT 1) AS amt
        FROM cos_adb.silver.events e
        LIMIT 20
        """
    )

    assert result.tables == [
        "cos_adb.silver.events",
        "cos_adb.silver.hr_payroll_summary",
    ]


def test_validate_basic_select_sql_still_rejects_dml() -> None:
    with pytest.raises(SqlValidationError):
        validate_basic_select_sql("DELETE FROM cos_adb.silver.events")


def test_validate_select_sql_rejects_unknown_column() -> None:
    with pytest.raises(SqlValidationError, match="manual_id"):
        validate_select_sql(
            "SELECT manual_id FROM cos_adb.silver.products",
            ALLOWED,
            table_columns=COLUMNS,
        )


def test_validate_select_sql_allows_known_unqualified_columns() -> None:
    result = validate_select_sql(
        "SELECT product_id, product_name FROM cos_adb.silver.products",
        ALLOWED,
        table_columns=COLUMNS,
    )

    assert result.sql.endswith("LIMIT 20")


def test_validate_select_sql_checks_alias_qualified_columns() -> None:
    with pytest.raises(SqlValidationError, match="p.manual_id"):
        validate_select_sql(
            "SELECT p.manual_id FROM cos_adb.silver.products p",
            ALLOWED,
            table_columns=COLUMNS,
        )


def test_build_safe_projection_sql_uses_real_business_columns() -> None:
    sql = build_safe_projection_sql(
        "SELECT manual_id FROM cos_adb.silver.products",
        ALLOWED,
        {
            "cos_adb.silver.products": [
                "_snapshot_id",
                "record_id",
                "product_id",
                "product_name",
                "status",
                "allowed_roles",
            ]
        },
    )

    assert sql == "SELECT record_id, product_id, product_name, status FROM cos_adb.silver.products LIMIT 20"
