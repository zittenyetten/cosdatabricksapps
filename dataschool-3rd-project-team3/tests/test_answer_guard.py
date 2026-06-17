from rbac_rag.answer_guard import validate_answer_summary


def test_answer_guard_blocks_non_executed_payroll_summary() -> None:
    summary = """
    ```sql
    SELECT e.event_id,
           (SELECT p.base_salary
            FROM cos_adb.silver.hr_payroll_summary p
            WHERE p.employee_id = e.owner_employee_id
            LIMIT 1) AS amt
    FROM cos_adb.silver.events e
    LIMIT 20
    ```

    | event_id | amt |
    | EVT-2026-001 | 7200000 |
    """

    result = validate_answer_summary(
        summary,
        role_id="MARKETING_STAFF",
        catalog="cos_adb",
        executed_sql="SELECT event_id, owner_employee_id FROM cos_adb.silver.events LIMIT 20",
        referenced_tables=["cos_adb.silver.events"],
        returned_columns=["event_id", "owner_employee_id"],
        results_text="event_id owner_employee_id\nEVT-2026-001 E20260003",
    )

    assert result.allowed is False
    assert any("non-executed tables" in reason for reason in result.reasons)
    assert any("sensitive tables" in reason for reason in result.reasons)
    assert any("not returned" in reason for reason in result.reasons)
    assert any("numeric values" in reason for reason in result.reasons)


def test_answer_guard_allows_summary_from_returned_columns() -> None:
    result = validate_answer_summary(
        "이벤트 EVT-2026-001은 OPEN 상태이며 담당자 ID는 E20260003입니다.",
        role_id="MARKETING_STAFF",
        catalog="cos_adb",
        executed_sql=(
            "SELECT event_id, owner_employee_id, status "
            "FROM cos_adb.silver.events LIMIT 20"
        ),
        referenced_tables=["cos_adb.silver.events"],
        returned_columns=["event_id", "owner_employee_id", "status"],
        results_text="event_id owner_employee_id status\nEVT-2026-001 E20260003 OPEN",
    )

    assert result.allowed is True
