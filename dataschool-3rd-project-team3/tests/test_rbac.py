import pytest

from rbac_rag.rbac import get_role_allowed_tables, validate_role_id


def test_validate_role_id_accepts_known_role() -> None:
    assert validate_role_id("GENERAL_EMPLOYEE", ["GENERAL_EMPLOYEE"]) == "GENERAL_EMPLOYEE"


def test_validate_role_id_strips_spaces() -> None:
    assert validate_role_id(" GENERAL_EMPLOYEE ", ["GENERAL_EMPLOYEE"]) == "GENERAL_EMPLOYEE"


def test_validate_role_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_role_id("", ["GENERAL_EMPLOYEE"])


def test_validate_role_id_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        validate_role_id("ADMIN", ["GENERAL_EMPLOYEE"])


def test_validate_role_id_rejects_invalid_characters() -> None:
    with pytest.raises(ValueError):
        validate_role_id("GENERAL_EMPLOYEE'; DROP TABLE x; --", ["GENERAL_EMPLOYEE"])


def test_marketing_staff_role_allowlist_excludes_payroll_tables() -> None:
    tables = get_role_allowed_tables("MARKETING_STAFF", "cos_adb")

    assert "cos_adb.silver.events" in tables
    assert "cos_adb.silver.mkt_campaign_plan" in tables
    assert "cos_adb.silver.voc_review_voc_insights" in tables
    assert "cos_adb.silver.hr_payroll_summary" not in tables
