import base64
import re
from dataclasses import dataclass
from typing import Any, Collection


SYSTEM_TO_DOMAINS = {
    "HRIS": ["HR"],
    "PLM": ["R&D/Product"],
    "QMS": ["Quality/RA", "Legal/Compliance"],
    "MES": ["Manufacturing"],
    "LIMS": ["Quality/RA"],
    "ERP": ["Finance", "SCM", "Distribution", "Customer Service", "Marketing"],
    "GROUPWARE": ["Event", "VOC"],
    "IAM": ["Security/Governance", "Metadata/Governance"],
    "FILE_STORAGE": ["Legal/Compliance"],
}


UNIVERSAL_DOMAINS = ["Master/Governance", "Evaluation"]

ROLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")

ROLE_ALLOWED_TABLES = {
    "COMPLIANCE_MANAGER": [
        "silver.compliance_audit_logs",
        "silver.regulatory_policy_documents",
        "governance.role_change_history",
    ],
    "CS_STAFF": [
        "silver.customer_inquiries",
        "silver.voc_review_insights",
        "silver.cs_response_manuals",
    ],
    "EXECUTIVE": [
        "gold.executive_kpi_summary",
        "gold.company_strategy_brief",
        "gold.investment_decision_summary",
    ],
    "FINANCE_MANAGER": [
        "silver.finance_sales_summary",
        "silver.finance_budget_plan",
        "silver.finance_expense_records",
    ],
    "FINANCE_STAFF": [
        "silver.finance_sales_summary",
        "silver.finance_expense_records",
    ],
    "GENERAL_EMPLOYEE": [
        "silver.events",
        "search.llm_table_context",
    ],
    "HR_MANAGER": [
        "silver.hr_employee_master",
        "silver.hr_performance_reviews",
        "silver.hr_training_records",
    ],
    "HR_STAFF": [
        "silver.hr_employee_master",
        "silver.hr_attendance_records",
        "silver.hr_training_records",
    ],
    "IT_ADMIN": [
        "governance.rag_identity_map",
        "governance.role_change_history",
        "governance.access_policies",
    ],
    "LEGAL_STAFF": [
        "silver.legal_contracts",
        "silver.regulatory_policy_documents",
    ],
    "MARKETING_STAFF": [
        "silver.events",
        "silver.marketing_campaign_plan",
        "silver.voc_review_insights",
    ],
    "PAYROLL_MANAGER": [
        "silver.hr_payroll_summary",
        "silver.compensation_adjustments",
    ],
    "PRODUCTION_MANAGER": [
        "silver.production_plan",
        "silver.manufacturing_work_orders",
        "silver.equipment_logs",
    ],
    "PRODUCTION_STAFF": [
        "silver.manufacturing_work_orders",
        "silver.batch_manufacturing_records",
        "silver.equipment_logs",
    ],
    "QA_MANAGER": [
        "silver.qa_deviation_reports",
        "silver.qa_capa_records",
        "silver.qa_qc_test_results",
    ],
    "QA_STAFF": [
        "silver.qa_deviation_reports",
        "silver.qa_capa_records",
    ],
    "QC_ANALYST": [
        "silver.qa_qc_test_results",
        "silver.lims_test_records",
    ],
    "RA_MANAGER": [
        "silver.ra_certification_documents",
        "silver.regulatory_risk_register",
    ],
    "RA_STAFF": [
        "silver.ra_labeling_review",
        "silver.marketing_claim_review",
    ],
    "RND_MANAGER": [
        "silver.rnd_product_master",
        "silver.rnd_formula_records",
        "silver.rnd_product_improvement_actions",
    ],
    "RND_RESEARCHER": [
        "silver.rnd_product_master",
        "silver.rnd_product_improvement_actions",
        "silver.qa_qc_test_results",
    ],
    "SCM_MANAGER": [
        "silver.scm_supplier_master",
        "silver.inventory_policy",
        "silver.distribution_schedule",
    ],
    "SCM_STAFF": [
        "silver.purchase_orders",
        "silver.inventory_transactions",
        "silver.distribution_schedule",
    ],
    "TRAINING_MANAGER": [
        "silver.training_completion_records",
        "silver.employee_certifications",
    ],
}


@dataclass
class WidgetInput:
    question: str
    role_id: str
    rbac_enabled: bool
    post_check_enabled: bool


def validate_role_id(role_id: str | None, valid_role_ids: Collection[str] | None = None) -> str:
    normalized = (role_id or "").strip()
    if not normalized:
        raise ValueError("role_id is required")
    if not ROLE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid role_id format: {normalized!r}")
    if valid_role_ids is not None and normalized not in set(valid_role_ids):
        raise ValueError(f"Unknown role_id: {normalized}")
    return normalized


def get_allowed_domains(
    spark: Any,
    role_id: str,
    valid_role_ids: Collection[str] | None = None,
    catalog: str = "cos_adb",
) -> list[str]:
    role_id = validate_role_id(role_id, valid_role_ids)
    rows = spark.sql(
        f"""
        SELECT DISTINCT system_name
        FROM {catalog}.governance.access_policies
        WHERE role_id = :role_id
        """,
        args={"role_id": role_id},
    ).collect()

    domains = set(UNIVERSAL_DOMAINS)
    for row in rows:
        domains.update(SYSTEM_TO_DOMAINS.get(row.system_name, []))
    return sorted(domains)


def list_role_ids(spark: Any, catalog: str = "cos_adb") -> list[str]:
    return [
        row.role_id
        for row in spark.sql(f"SELECT role_id FROM {catalog}.silver.roles ORDER BY role_id").collect()
    ]


def get_role_allowed_tables(role_id: str, catalog: str = "cos_adb") -> set[str]:
    table_suffixes = ROLE_ALLOWED_TABLES.get(role_id, [])
    return {f"{catalog}.{suffix}" for suffix in table_suffixes}


def parse_widget_input(dbutils: Any) -> WidgetInput:
    question_b64 = dbutils.widgets.get("question_b64")
    question_encoding = dbutils.widgets.get("question_encoding")

    if question_b64 and question_encoding == "base64_utf8":
        question = base64.b64decode(question_b64).decode("utf-8")
    else:
        question = dbutils.widgets.get("question")

    return WidgetInput(
        question=question,
        role_id=dbutils.widgets.get("role_id"),
        rbac_enabled=dbutils.widgets.get("rbac_enabled") == "ON",
        post_check_enabled=dbutils.widgets.get("post_check") == "ON",
    )


def ensure_widgets(dbutils: Any, role_ids: list[str]) -> None:
    dbutils.widgets.text("question", "")
    dbutils.widgets.text("question_b64", "")
    dbutils.widgets.text("question_encoding", "")
    dbutils.widgets.text("role_id", "GENERAL_EMPLOYEE")

    try:
        job_role = dbutils.widgets.get("role_id")
    except Exception:
        job_role = "GENERAL_EMPLOYEE"

    try:
        dbutils.widgets.dropdown("rbac_enabled", "ON", ["ON", "OFF"], "RBAC")
    except Exception:
        pass

    try:
        dbutils.widgets.dropdown("post_check", "ON", ["ON", "OFF"], "Post-Check")
    except Exception:
        pass

    try:
        dbutils.widgets.dropdown("user_role", job_role, role_ids, "Role")
    except Exception:
        pass


def resolve_selected_role(dbutils: Any) -> str:
    try:
        return dbutils.widgets.get("role_id")
    except Exception:
        return dbutils.widgets.get("user_role")
