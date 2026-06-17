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
