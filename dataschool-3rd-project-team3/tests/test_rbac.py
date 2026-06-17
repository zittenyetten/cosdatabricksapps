import pytest
from types import SimpleNamespace

from rbac_rag.rbac import FALLBACK_ROLE_ALLOWED_TABLES, get_role_allowed_tables, get_role_table_access, get_sensitive_table_denials, validate_role_id


COS_ADB_TABLE_SNAPSHOT = {
    "cos_adb.governance.access_policies",
    "cos_adb.governance.rag_identity_map",
    "cos_adb.governance.role_change_history",
    "cos_adb.silver.cs_customer_inquiries",
    "cos_adb.silver.departments",
    "cos_adb.silver.dist_channel_distribution_status",
    "cos_adb.silver.dist_finished_goods_inventory",
    "cos_adb.silver.employees",
    "cos_adb.silver.events",
    "cos_adb.silver.fin_budget_plan",
    "cos_adb.silver.fin_campaign_sales_attribution",
    "cos_adb.silver.fin_expense_records",
    "cos_adb.silver.fin_sales_summary",
    "cos_adb.silver.hr_payroll_summary",
    "cos_adb.silver.legal_compliance_audit_log",
    "cos_adb.silver.legal_contract_metadata",
    "cos_adb.silver.legal_privacy_policy_documents",
    "cos_adb.silver.legal_regulatory_documents",
    "cos_adb.silver.mfg_batch_manufacturing_records",
    "cos_adb.silver.mfg_production_plan",
    "cos_adb.silver.mfg_work_orders",
    "cos_adb.silver.mkt_ad_copy_review",
    "cos_adb.silver.mkt_campaign_plan",
    "cos_adb.silver.mkt_product_launch_calendar",
    "cos_adb.silver.mkt_sns_performance",
    "cos_adb.silver.qa_capa_records",
    "cos_adb.silver.qa_deviation_reports",
    "cos_adb.silver.qa_qc_test_results",
    "cos_adb.silver.rnd_product_improvement_actions",
    "cos_adb.silver.rnd_product_master",
    "cos_adb.silver.scm_delivery_schedule",
    "cos_adb.silver.scm_purchase_orders",
    "cos_adb.silver.scm_raw_material_inventory",
    "cos_adb.silver.scm_supplier_master",
    "cos_adb.silver.voc_review_voc_insights",
}


class FakeResult:
    def __init__(self, rows):
        self.rows = [SimpleNamespace(**row) for row in rows]

    def collect(self):
        return self.rows


class FakePolicySql:
    def sql(self, statement, args=None):
        if "role_table_permissions" in statement:
            return FakeResult(
                [
                    {"table_fqn": "cos_adb.silver.events"},
                    {"table_fqn": "cos_adb.silver.hr_payroll_summary"},
                ]
            )
        if "information_schema.tables" in statement:
            return FakeResult(
                [
                    {"fqn": "cos_adb.silver.events"},
                    {"fqn": "cos_adb.silver.hr_payroll_summary"},
                ]
            )
        raise AssertionError(f"unexpected query: {statement}")


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


def test_fallback_role_allowlists_match_cos_adb_snapshot() -> None:
    assert FALLBACK_ROLE_ALLOWED_TABLES
    for role_id in FALLBACK_ROLE_ALLOWED_TABLES:
        tables = get_role_allowed_tables(role_id, "cos_adb")
        assert tables
        assert tables <= COS_ADB_TABLE_SNAPSHOT


def test_role_table_access_prefers_governance_policy() -> None:
    access = get_role_table_access(
        FakePolicySql(),
        "MARKETING_STAFF",
        ["MARKETING_STAFF"],
        "cos_adb",
    )

    assert access.source == "cos_adb.governance.role_table_permissions"
    assert access.fallback_used is False
    assert access.tables == {
        "cos_adb.silver.events",
        "cos_adb.silver.hr_payroll_summary",
    }


def test_sensitive_table_denials_block_payroll_for_non_hr_roles() -> None:
    assert get_sensitive_table_denials(
        "MARKETING_STAFF",
        ["cos_adb.silver.events", "cos_adb.silver.hr_payroll_summary"],
        "cos_adb",
    ) == ["cos_adb.silver.hr_payroll_summary"]

    assert get_sensitive_table_denials(
        "PAYROLL_MANAGER",
        ["cos_adb.silver.hr_payroll_summary"],
        "cos_adb",
    ) == []
