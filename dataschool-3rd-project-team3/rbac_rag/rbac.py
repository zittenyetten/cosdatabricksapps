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

ROLE_TABLE_POLICY_TABLE = "governance.role_table_permissions"

SENSITIVE_TABLE_ROLE_ALLOWLIST = {
    "silver.hr_payroll_summary": {"HR_MANAGER", "PAYROLL_MANAGER"},
}

FALLBACK_ROLE_ALLOWED_TABLES = {
    "COMPLIANCE_MANAGER": [
        "silver.legal_compliance_audit_log",
        "silver.legal_regulatory_documents",
        "governance.role_change_history",
    ],
    "CS_STAFF": [
        "silver.cs_customer_inquiries",
        "silver.voc_review_voc_insights",
    ],
    "EXECUTIVE": [
        "silver.fin_sales_summary",
        "silver.fin_budget_plan",
        "silver.fin_campaign_sales_attribution",
        "silver.mkt_campaign_plan",
        "silver.qa_qc_test_results",
        "silver.rnd_product_master",
    ],
    "FINANCE_MANAGER": [
        "silver.fin_sales_summary",
        "silver.fin_budget_plan",
        "silver.fin_expense_records",
    ],
    "FINANCE_STAFF": [
        "silver.fin_sales_summary",
        "silver.fin_expense_records",
    ],
    "GENERAL_EMPLOYEE": [
        "silver.events",
    ],
    "HR_MANAGER": [
        "silver.employees",
        "silver.departments",
        "silver.hr_payroll_summary",
    ],
    "HR_STAFF": [
        "silver.employees",
        "silver.departments",
    ],
    "IT_ADMIN": [
        "governance.rag_identity_map",
        "governance.role_change_history",
        "governance.access_policies",
    ],
    "LEGAL_STAFF": [
        "silver.legal_contract_metadata",
        "silver.legal_regulatory_documents",
    ],
    "MARKETING_STAFF": [
        "silver.events",
        "silver.mkt_ad_copy_review",
        "silver.mkt_campaign_plan",
        "silver.mkt_product_launch_calendar",
        "silver.mkt_sns_performance",
        "silver.voc_review_voc_insights",
    ],
    "PAYROLL_MANAGER": [
        "silver.employees",
        "silver.hr_payroll_summary",
    ],
    "PRODUCTION_MANAGER": [
        "silver.mfg_batch_manufacturing_records",
        "silver.mfg_production_plan",
        "silver.mfg_work_orders",
    ],
    "PRODUCTION_STAFF": [
        "silver.mfg_batch_manufacturing_records",
        "silver.mfg_work_orders",
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
    ],
    "RA_MANAGER": [
        "silver.legal_privacy_policy_documents",
        "silver.legal_regulatory_documents",
        "silver.mkt_ad_copy_review",
    ],
    "RA_STAFF": [
        "silver.legal_regulatory_documents",
        "silver.mkt_ad_copy_review",
    ],
    "RND_MANAGER": [
        "silver.rnd_product_master",
        "silver.rnd_product_improvement_actions",
        "silver.qa_qc_test_results",
    ],
    "RND_RESEARCHER": [
        "silver.rnd_product_master",
        "silver.rnd_product_improvement_actions",
        "silver.qa_qc_test_results",
    ],
    "SCM_MANAGER": [
        "silver.dist_channel_distribution_status",
        "silver.dist_finished_goods_inventory",
        "silver.scm_delivery_schedule",
        "silver.scm_raw_material_inventory",
        "silver.scm_supplier_master",
    ],
    "SCM_STAFF": [
        "silver.scm_delivery_schedule",
        "silver.scm_purchase_orders",
        "silver.scm_raw_material_inventory",
    ],
    "TRAINING_MANAGER": [
        "silver.departments",
        "silver.employees",
    ],
}


@dataclass
class WidgetInput:
    question: str
    role_id: str
    rbac_enabled: bool
    post_check_enabled: bool


@dataclass(frozen=True)
class RoleTableAccess:
    role_id: str
    tables: set[str]
    source: str
    fallback_used: bool
    missing_tables: list[str]
    warnings: list[str]


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
    table_suffixes = FALLBACK_ROLE_ALLOWED_TABLES.get(role_id, [])
    return {f"{catalog}.{suffix}" for suffix in table_suffixes}


def get_sensitive_table_denials(
    role_id: str | None,
    tables: Collection[str],
    catalog: str = "cos_adb",
) -> list[str]:
    if not role_id:
        return []
    normalized_role = role_id.strip()
    denied: list[str] = []
    for suffix, allowed_roles in SENSITIVE_TABLE_ROLE_ALLOWLIST.items():
        fqn = f"{catalog}.{suffix}".lower()
        if any(str(table).lower() == fqn for table in tables) and normalized_role not in allowed_roles:
            denied.append(f"{catalog}.{suffix}")
    return sorted(denied)


def get_role_table_access(
    spark: Any,
    role_id: str,
    valid_role_ids: Collection[str] | None = None,
    catalog: str = "cos_adb",
) -> RoleTableAccess:
    role_id = validate_role_id(role_id, valid_role_ids)
    warnings: list[str] = []
    fallback_used = False

    try:
        rows = spark.sql(
            f"""
            SELECT table_fqn
            FROM {catalog}.{ROLE_TABLE_POLICY_TABLE}
            WHERE role_id = :role_id
              AND COALESCE(is_active, true) = true
            ORDER BY table_fqn
            """,
            args={"role_id": role_id},
        ).collect()
        tables = set()
        for row in rows:
            table_fqn = getattr(row, "table_fqn", None)
            if table_fqn:
                tables.add(str(table_fqn).strip())
        source = f"{catalog}.{ROLE_TABLE_POLICY_TABLE}"
        if not tables:
            fallback_used = True
            source = "fallback:FALLBACK_ROLE_ALLOWED_TABLES"
            warnings.append("No active role_table_permissions rows; using repo fallback.")
            tables = get_role_allowed_tables(role_id, catalog)
    except Exception as error:
        fallback_used = True
        source = "fallback:FALLBACK_ROLE_ALLOWED_TABLES"
        warnings.append(
            f"{catalog}.{ROLE_TABLE_POLICY_TABLE} unavailable; using repo fallback ({error.__class__.__name__})."
        )
        tables = get_role_allowed_tables(role_id, catalog)

    catalog_tables = _list_catalog_tables(spark, catalog, warnings)
    missing_tables: list[str] = []
    if catalog_tables is not None:
        missing_tables = sorted(tables - catalog_tables)
        tables = tables.intersection(catalog_tables)

    return RoleTableAccess(
        role_id=role_id,
        tables=tables,
        source=source,
        fallback_used=fallback_used,
        missing_tables=missing_tables,
        warnings=warnings,
    )


def _list_catalog_tables(
    spark: Any,
    catalog: str,
    warnings: list[str],
) -> set[str] | None:
    try:
        rows = spark.sql(
            f"""
            SELECT CONCAT('{catalog}.', table_schema, '.', table_name) AS fqn
            FROM {catalog}.information_schema.tables
            WHERE table_schema != 'information_schema'
            """
        ).collect()
    except Exception as error:
        warnings.append(f"Catalog table validation skipped ({error.__class__.__name__}).")
        return None
    tables = set()
    for row in rows:
        fqn = getattr(row, "fqn", None)
        if fqn:
            tables.add(str(fqn).strip())
    return tables


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
