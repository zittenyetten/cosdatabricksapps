import asyncio
import base64
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Literal
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.admin_demo_service import AdminLayeredDemoService
import rbac_rag.engine as rag_engine_module
import rbac_rag.rbac as rbac_module
import rbac_rag.sql_validator as sql_validator_module
from rbac_rag.api_service import RagApiService
from rbac_rag.rbac import (
    get_allowed_domains,
    get_role_table_access,
    get_sensitive_table_denials,
    validate_role_id,
)
from rbac_rag.sql_validator import SqlValidationError, validate_select_sql


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(PROJECT_ROOT / "dataschool-3rd-project-team3" / ".env")
APP_BUILD_ID = "admin-layered-demo-2026-06-17"

app = FastAPI(title="COSBELLE RAG Console")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class ChatRequest(BaseModel):
    endpoint: str = "/api/answer"
    query: str
    role_id: str = "GENERAL_EMPLOYEE"
    rbac_enabled: bool = True
    pre_check_enabled: bool = True
    post_check_enabled: bool = True


class SimulateRequest(BaseModel):
    role_id: str
    department_name: str
    security_clearance: str
    query: str
    rbac_enabled: bool = True
    pre_check_enabled: bool = True
    post_check_enabled: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


class RagChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    role_id: str = Field(..., min_length=1)
    mode: Literal["auto", "chat", "work"] = "auto"
    rbac_enabled: bool = True
    post_check: bool = True
    top_k: int | None = Field(default=None, ge=1, le=20)


ROLE_ROWS = [
    ("COMPLIANCE_MANAGER", "Compliance Manager", "Compliance and audit log management", "RESTRICTED", "Compliance", ["QMS", "GROUPWARE"], ["Legal/Compliance", "Quality/RA", "Audit"], ["cos_adb.silver.legal_compliance_audit_log", "cos_adb.silver.legal_regulatory_documents", "cos_adb.governance.role_change_history"]),
    ("CS_STAFF", "CS Staff", "Customer inquiry and response manual management", "CONFIDENTIAL", "CS", ["CRM", "GROUPWARE"], ["Customer Service", "VOC", "Event"], ["cos_adb.silver.cs_customer_inquiries", "cos_adb.silver.voc_review_voc_insights"]),
    ("EXECUTIVE", "Executive", "Company KPI, strategy, and investment decisions", "RESTRICTED", "Executive", ["ERP", "QMS", "PLM", "GROUPWARE"], ["Executive", "Finance", "Marketing", "Quality/RA", "R&D/Product"], ["cos_adb.silver.fin_sales_summary", "cos_adb.silver.fin_budget_plan", "cos_adb.silver.fin_campaign_sales_attribution", "cos_adb.silver.mkt_campaign_plan", "cos_adb.silver.qa_qc_test_results", "cos_adb.silver.rnd_product_master"]),
    ("FINANCE_MANAGER", "Finance Manager", "Tax, budget, and investment material approval", "RESTRICTED", "Finance", ["ERP"], ["Finance", "SCM", "Distribution"], ["cos_adb.silver.fin_sales_summary", "cos_adb.silver.fin_budget_plan", "cos_adb.silver.fin_expense_records"]),
    ("FINANCE_STAFF", "Finance Staff", "Expense, budget, and sales aggregation", "CONFIDENTIAL", "Finance", ["ERP"], ["Finance"], ["cos_adb.silver.fin_sales_summary", "cos_adb.silver.fin_expense_records"]),
    ("GENERAL_EMPLOYEE", "General Employee", "Internal notice and allowed department material access", "INTERNAL", "General", ["GROUPWARE"], ["Event", "Notice"], ["cos_adb.silver.events"]),
    ("HR_MANAGER", "HR Manager", "HR approval, evaluation, and privacy control", "RESTRICTED", "HR", ["HRIS"], ["HR"], ["cos_adb.silver.employees", "cos_adb.silver.departments", "cos_adb.silver.hr_payroll_summary"]),
    ("HR_STAFF", "HR Staff", "HR operations, attendance, and training data handling", "CONFIDENTIAL", "HR", ["HRIS"], ["HR", "Training"], ["cos_adb.silver.employees", "cos_adb.silver.departments"]),
    ("IT_ADMIN", "IT Admin", "IAM, account, and permission management", "RESTRICTED", "IT", ["IAM", "GROUPWARE"], ["IAM", "Security", "Governance"], ["cos_adb.governance.rag_identity_map", "cos_adb.governance.role_change_history", "cos_adb.governance.access_policies"]),
    ("LEGAL_STAFF", "Legal Staff", "Contract and legal document review", "RESTRICTED", "Legal", ["GROUPWARE"], ["Legal/Compliance"], ["cos_adb.silver.legal_contract_metadata", "cos_adb.silver.legal_regulatory_documents"]),
    ("MARKETING_STAFF", "Marketing Staff", "Campaign, ad copy, and launch schedule management", "DEPARTMENT", "Marketing", ["ERP", "GROUPWARE"], ["Marketing", "VOC", "Event"], ["cos_adb.silver.events", "cos_adb.silver.mkt_ad_copy_review", "cos_adb.silver.mkt_campaign_plan", "cos_adb.silver.mkt_product_launch_calendar", "cos_adb.silver.mkt_sns_performance", "cos_adb.silver.voc_review_voc_insights"]),
    ("PAYROLL_MANAGER", "Payroll Manager", "Payroll summary and compensation data handling", "RESTRICTED", "HR", ["HRIS"], ["Payroll", "HR"], ["cos_adb.silver.employees", "cos_adb.silver.hr_payroll_summary"]),
    ("PRODUCTION_MANAGER", "Production Manager", "Production planning and work approval", "CONFIDENTIAL", "Production", ["MES", "ERP"], ["Manufacturing", "SCM"], ["cos_adb.silver.mfg_batch_manufacturing_records", "cos_adb.silver.mfg_production_plan", "cos_adb.silver.mfg_work_orders"]),
    ("PRODUCTION_STAFF", "Production Staff", "Work orders, manufacturing records, and equipment logs", "CONFIDENTIAL", "Production", ["MES"], ["Manufacturing"], ["cos_adb.silver.mfg_batch_manufacturing_records", "cos_adb.silver.mfg_work_orders"]),
    ("QA_MANAGER", "QA Manager", "Quality approval and audit response", "RESTRICTED", "QA", ["QMS", "LIMS", "GROUPWARE"], ["Quality/RA", "Manufacturing", "Event"], ["cos_adb.silver.qa_deviation_reports", "cos_adb.silver.qa_capa_records", "cos_adb.silver.qa_qc_test_results"]),
    ("QA_STAFF", "QA Staff", "Quality documents, deviation, and CAPA management", "CONFIDENTIAL", "QA", ["QMS"], ["Quality/RA"], ["cos_adb.silver.qa_deviation_reports", "cos_adb.silver.qa_capa_records"]),
    ("QC_ANALYST", "QC Analyst", "Test result and LIMS record management", "CONFIDENTIAL", "QC", ["LIMS", "QMS"], ["Quality/RA"], ["cos_adb.silver.qa_qc_test_results"]),
    ("RA_MANAGER", "RA Manager", "Regulatory risk and certification document approval", "RESTRICTED", "RA", ["QMS", "GROUPWARE"], ["Legal/Compliance", "Quality/RA"], ["cos_adb.silver.legal_privacy_policy_documents", "cos_adb.silver.legal_regulatory_documents", "cos_adb.silver.mkt_ad_copy_review"]),
    ("RA_STAFF", "RA Staff", "Labeling, advertising, and regulatory review", "CONFIDENTIAL", "RA", ["QMS", "GROUPWARE"], ["Legal/Compliance", "Marketing"], ["cos_adb.silver.legal_regulatory_documents", "cos_adb.silver.mkt_ad_copy_review"]),
    ("RND_MANAGER", "R&D Manager", "Research task and formula approval", "RESTRICTED", "R&D", ["PLM", "QMS"], ["R&D/Product", "Quality/RA"], ["cos_adb.silver.rnd_product_master", "cos_adb.silver.rnd_product_improvement_actions", "cos_adb.silver.qa_qc_test_results"]),
    ("RND_RESEARCHER", "R&D Researcher", "Product planning, formula development, and test records", "CONFIDENTIAL", "R&D", ["PLM", "QMS"], ["R&D/Product", "Quality/RA"], ["cos_adb.silver.rnd_product_master", "cos_adb.silver.rnd_product_improvement_actions", "cos_adb.silver.qa_qc_test_results"]),
    ("SCM_MANAGER", "SCM Manager", "Supplier and inventory policy approval", "CONFIDENTIAL", "SCM", ["ERP", "MES"], ["SCM", "Distribution", "Manufacturing"], ["cos_adb.silver.dist_channel_distribution_status", "cos_adb.silver.dist_finished_goods_inventory", "cos_adb.silver.scm_delivery_schedule", "cos_adb.silver.scm_raw_material_inventory", "cos_adb.silver.scm_supplier_master"]),
    ("SCM_STAFF", "SCM Staff", "Purchase order, inventory, and logistics schedule management", "CONFIDENTIAL", "SCM", ["ERP"], ["SCM", "Distribution"], ["cos_adb.silver.scm_delivery_schedule", "cos_adb.silver.scm_purchase_orders", "cos_adb.silver.scm_raw_material_inventory"]),
    ("TRAINING_MANAGER", "Training Manager", "Training completion, certification, and required education management", "CONFIDENTIAL", "Training", ["HRIS", "GROUPWARE"], ["Training", "HR"], ["cos_adb.silver.departments", "cos_adb.silver.employees"]),
]


ROLE_ACCESS = {
    role_id: {
        "role_name": role_name,
        "description": description,
        "default_clearance": clearance,
        "department": department,
        "systems": systems,
        "domains": domains,
        "tables": tables,
    }
    for role_id, role_name, description, clearance, department, systems, domains, tables in ROLE_ROWS
}

RECENT_RESPONSES: list[dict[str, Any]] = []
RECENT_SQL_LOGS: list[dict[str, Any]] = []


@lru_cache(maxsize=1)
def get_rag_service() -> RagApiService:
    return RagApiService()


def get_admin_demo_service() -> AdminLayeredDemoService:
    return AdminLayeredDemoService(get_rag_service())


def databricks_configured() -> bool:
    has_host = bool(os.getenv("DATABRICKS_SERVER_HOSTNAME") or os.getenv("DATABRICKS_HOST"))
    has_sql_compute = bool(os.getenv("DATABRICKS_HTTP_PATH") or os.getenv("DATABRICKS_WAREHOUSE_ID"))
    has_credentials = bool(
        (os.getenv("DATABRICKS_CLIENT_ID") and os.getenv("DATABRICKS_CLIENT_SECRET"))
        or os.getenv("DATABRICKS_TOKEN")
    )
    return bool(
        has_host
        and has_sql_compute
        and has_credentials
    )


@lru_cache(maxsize=1)
def source_revision() -> str:
    for key in ("APP_BUILD_SHA", "GIT_COMMIT", "SOURCE_VERSION", "DATABRICKS_GIT_COMMIT"):
        value = os.getenv(key, "").strip()
        if value:
            return value

    try:
        completed = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
        return completed.stdout.strip()
    except Exception:
        return "unknown"


def build_info() -> dict[str, Any]:
    return {
        "build_id": APP_BUILD_ID,
        "source_revision": source_revision(),
        "runtime_modules": {
            "rbac_rag.engine": getattr(rag_engine_module, "__file__", "unknown"),
            "rbac_rag.rbac": getattr(rbac_module, "__file__", "unknown"),
            "rbac_rag.sql_validator": getattr(sql_validator_module, "__file__", "unknown"),
        },
        "guard_features": [
            "role_table_intersection",
            "subquery_table_validation",
            "post_check_failure_aliases",
            "public_error_redaction",
            "catalog_table_name_sync",
            "cos_adb_role_table_policy",
            "server_side_role_and_rbac_lock",
            "sensitive_table_role_guard",
            "answer_summary_result_guard",
            "admin_post_check_toggle",
            "admin_layered_demo_pipeline",
        ],
    }


def build_salary_subquery_probe(service: Any, effective_allowed_tables: set[str]) -> dict[str, Any]:
    catalog = service.settings.catalog
    probe_sql = f"""
    SELECT e.event_id,
           (SELECT p.base_salary
            FROM {catalog}.silver.hr_payroll_summary p
            WHERE p.employee_id = e.owner_employee_id
            LIMIT 1) AS amt
    FROM {catalog}.silver.events e
    LIMIT 20
    """
    try:
        validate_select_sql(
            probe_sql,
            effective_allowed_tables,
            table_columns=service.mappings.table_columns,
        )
    except SqlValidationError as exc:
        return {
            "expected": "BLOCKED",
            "actual": "BLOCKED",
            "stage": "table_validation",
            "detail": str(exc),
        }
    sensitive_denials = get_sensitive_table_denials(
        "MARKETING_STAFF",
        [f"{catalog}.silver.hr_payroll_summary"],
        catalog,
    )
    if sensitive_denials:
        return {
            "expected": "BLOCKED",
            "actual": "BLOCKED",
            "stage": "sensitive_table_guard",
            "detail": (
                "SQL references sensitive tables not allowed for role MARKETING_STAFF: "
                + ", ".join(sensitive_denials)
            ),
        }
    return {
        "expected": "BLOCKED",
        "actual": "ALLOWED",
        "detail": "Salary subquery was allowed by the current effective table set.",
    }


def build_sensitive_table_probe(service: Any, role_id: str, tables: list[str]) -> dict[str, Any]:
    denials = get_sensitive_table_denials(role_id, tables, service.settings.catalog)
    return {
        "role_id": role_id,
        "tables": tables,
        "expected": "BLOCKED" if denials else "ALLOWED",
        "actual": "BLOCKED" if denials else "ALLOWED",
        "denials": denials,
    }


def safe_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    for key in ("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_SECRET", "RAG_API_TOKEN"):
        value = os.getenv(key, "").strip()
        if value:
            message = message.replace(value, "[REDACTED]")
    message = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", message, flags=re.IGNORECASE)
    return message[:300]


def build_check_result(rbac_enabled: bool, pre_check_enabled: bool, post_check_enabled: bool) -> dict:
    return {
        "rbac_enabled": rbac_enabled,
        "pre_check": "PASS" if pre_check_enabled else "SKIPPED",
        "pre_check_message": "Allowed domains and tables were used for retrieval." if pre_check_enabled else "Pre-check was skipped.",
        "post_check": "PASS" if post_check_enabled else "SKIPPED",
        "post_check_message": "The generated answer was checked against the current role." if post_check_enabled else "Post-check was skipped.",
    }


def role_dashboard_metrics(role_id: str) -> dict:
    seed = sum(ord(char) for char in role_id)
    requests = 70 + seed % 180
    blocked = 2 + seed % 18
    failed = seed % 7
    completed = requests - blocked - failed
    no_evidence = 1 + seed % 9
    post_blocked = seed % 6
    return {
        "requests": requests,
        "completed": completed,
        "blocked": blocked,
        "failed": failed,
        "pre_check_blocked": blocked - post_blocked if blocked >= post_blocked else blocked,
        "post_check_blocked": post_blocked,
        "no_evidence": no_evidence,
        "guard_pass_rate": f"{round((completed / requests) * 100, 1)}%",
    }


def role_blocked_attempts(role_id: str) -> list[dict]:
    restricted_candidates = {
        "MARKETING_STAFF": ["cos_adb.silver.rnd_product_master", "cos_adb.silver.hr_payroll_summary"],
        "GENERAL_EMPLOYEE": ["cos_adb.silver.hr_payroll_summary", "cos_adb.silver.rnd_product_master"],
        "RND_RESEARCHER": ["cos_adb.silver.fin_budget_plan", "cos_adb.silver.hr_payroll_summary"],
        "QA_STAFF": ["cos_adb.silver.hr_payroll_summary", "cos_adb.silver.fin_budget_plan"],
    }
    tables = restricted_candidates.get(role_id, ["cos_adb.silver.hr_payroll_summary", "cos_adb.silver.rnd_product_master"])
    return [{"table": table, "count": index + 1, "reason": "RBAC pre-check blocked"} for index, table in enumerate(tables)]


def normalize_sources(raw: dict[str, Any], fallback_tables: list[str], fallback_clearance: str, role_id: str) -> dict:
    raw_sources = raw.get("sources") or raw.get("citations") or {}
    if isinstance(raw_sources, list):
        documents = raw_sources
        tables = raw.get("tables") or fallback_tables[:2]
    else:
        documents = raw_sources.get("documents") if "documents" in raw_sources else raw.get("citations")
        tables = raw_sources.get("tables") if "tables" in raw_sources else raw.get("tables")
        documents = documents if documents is not None else []
        tables = tables if tables is not None else fallback_tables[:2]

    normalized_docs = []
    for index, doc in enumerate(documents):
        if isinstance(doc, str):
            normalized_docs.append({"document_id": doc, "chunk_id": "", "classification": fallback_clearance})
        else:
            normalized_docs.append(
                {
                    "document_id": doc.get("document_id") or doc.get("source_id") or f"DOC-{role_id}-{index + 1:03d}",
                    "chunk_id": doc.get("chunk_id") or doc.get("chunk") or "",
                    "classification": doc.get("classification") or doc.get("security_clearance") or fallback_clearance,
                }
            )
    return {"tables": tables, "documents": normalized_docs}


def execute_rag_chat(
    payload: dict[str, Any],
    *,
    event_callback=None,
    top_k: int | None = None,
) -> dict[str, Any]:
    return get_rag_service().chat(
        question=payload["query"],
        role_id=payload["role_id"],
        mode=payload.get("mode", "auto"),
        rbac_enabled=coerce_bool(payload.get("rbac_enabled", True)),
        post_check=coerce_bool(payload.get("post_check_enabled", payload.get("post_check", True))),
        top_k=top_k,
        event_callback=event_callback,
    )


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "off", "no", "n", ""}
    return bool(value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else coerce_bool(value)


def secure_request_payload(payload: dict[str, Any], mode: str) -> dict[str, Any]:
    secured = dict(payload)
    if mode == "user":
        if not env_bool("RBAC_RAG_ALLOW_PUBLIC_ROLE_SELECTION", False):
            secured["role_id"] = os.getenv("RBAC_RAG_PUBLIC_ROLE_ID", "GENERAL_EMPLOYEE").strip() or "GENERAL_EMPLOYEE"
        secured["rbac_enabled"] = True
        secured["pre_check_enabled"] = True
        secured["post_check_enabled"] = True
        secured["security_mode"] = "public_locked"
        return secured

    if mode == "admin_simulation" and not env_bool("RBAC_RAG_ALLOW_UNSAFE_ADMIN_SIMULATION", False):
        secured["rbac_enabled"] = True
        secured["pre_check_enabled"] = True
        secured["post_check_enabled"] = coerce_bool(
            secured.get("post_check_enabled", secured.get("post_check", True))
        )
        secured["security_mode"] = "admin_layered_demo"
        return secured

    return secured


def secure_native_request_payload(payload: RagChatRequest) -> dict[str, Any]:
    role_id = payload.role_id
    if not env_bool("RBAC_RAG_ALLOW_NATIVE_ROLE_SELECTION", False):
        role_id = os.getenv("RBAC_RAG_PUBLIC_ROLE_ID", "GENERAL_EMPLOYEE").strip() or "GENERAL_EMPLOYEE"
    unsafe_options_allowed = env_bool("RBAC_RAG_ALLOW_UNSAFE_NATIVE_OPTIONS", False)
    return {
        "question": payload.question,
        "role_id": role_id,
        "mode": payload.mode,
        "rbac_enabled": payload.rbac_enabled if unsafe_options_allowed else True,
        "post_check": payload.post_check if unsafe_options_allowed else True,
        "top_k": payload.top_k,
        "security_mode": "native_locked" if not unsafe_options_allowed else "native_unlocked",
    }


def format_rag_api_result(raw: dict[str, Any], payload: dict[str, Any], access: dict[str, Any]) -> dict:
    role_id = payload["role_id"]
    clearance = access["default_clearance"]
    blocked = bool(raw.get("blocked", False))
    raw_checks = raw.get("checks") if isinstance(raw.get("checks"), dict) else {}
    sources = {"tables": [], "documents": []} if blocked else normalize_sources(raw, access["tables"], clearance, role_id)
    return {
        "request_id": raw.get("request_id") or raw.get("id") or "REQ-RAG-LIVE",
        "guard_status": raw.get("guard_status") or raw.get("status") or "PASS",
        "answer_guard_status": (
            raw.get("answer_guard_status")
            or raw.get("post_check")
            or raw_checks.get("post_check")
            or "PASS"
        ),
        "blocked": blocked,
        "answer": raw.get("answer") or raw.get("response") or raw.get("summary") or "",
        "sources": sources,
        "checks": raw_checks or build_check_result(
            coerce_bool(payload.get("rbac_enabled", True)),
            coerce_bool(payload.get("pre_check_enabled", True)),
            coerce_bool(payload.get("post_check_enabled", payload.get("post_check", True))),
        ),
        "sql_log": raw.get("sql_log") or raw.get("log") or normalize_api_sql_log(raw),
        "raw": raw,
    }


INTERNAL_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:`?[A-Za-z_][A-Za-z0-9_-]*`?\.){2}`?[A-Za-z_][A-Za-z0-9_-]*`?\b"
)


def redact_public_response(response: dict[str, Any]) -> dict[str, Any]:
    public = dict(response)
    public["answer"] = public_safe_answer(public)

    sources = public.get("sources") if isinstance(public.get("sources"), dict) else {}
    tables = sources.get("tables") if isinstance(sources.get("tables"), list) else []
    public["sources"] = {
        "tables": ["권한이 허용된 업무 데이터"] if tables and not public.get("blocked") else [],
        "documents": [],
    }
    public["sql_log"] = {}
    public["raw"] = {"redacted": True}

    for key, value in {
        "generated_sql": None,
        "columns": [],
        "rows": [],
    }.items():
        if key in public:
            public[key] = value

    return public


def public_safe_answer(response: dict[str, Any]) -> str:
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    failure_reason = str(raw.get("failure_reason") or "")
    answer = str(response.get("answer") or "")
    guard_status = str(response.get("guard_status") or "").upper()

    if response.get("blocked"):
        if failure_reason in {"SQL_VALIDATION_ERROR", "SQL_COLUMN_VALIDATION_ERROR"} or has_internal_detail(answer):
            return "요청을 안전하게 처리할 수 없어 답변을 제공하지 못했습니다. 질문을 조금 더 구체적으로 다시 입력하거나 관리자에게 문의해 주세요."
        return "현재 역할 권한으로는 해당 요청에 대한 답변을 제공할 수 없습니다."

    if guard_status == "ERROR" or failure_reason in {"SQL_EXECUTION_ERROR", "SQL_COLUMN_VALIDATION_ERROR"}:
        return "조회 처리 중 오류가 발생했습니다. 질문을 조금 더 구체적으로 다시 입력하거나 관리자에게 문의해 주세요."

    return redact_internal_identifiers(answer)


def has_internal_detail(value: str) -> bool:
    lowered = value.lower()
    return bool(
        INTERNAL_IDENTIFIER_PATTERN.search(value)
        or "sql references " in lowered
        or "use only these columns" in lowered
        or "unavailable columns" in lowered
        or "non-allowed tables" in lowered
    )


def redact_internal_identifiers(value: str) -> str:
    return INTERNAL_IDENTIFIER_PATTERN.sub("내부 데이터", value)


def stream_error_payload(exc: Exception, status: int, mode: str) -> dict[str, Any]:
    detail = safe_error(exc)
    if mode == "user" and has_internal_detail(detail):
        detail = "요청 처리 중 오류가 발생했습니다. 질문을 조금 더 구체적으로 다시 입력하거나 관리자에게 문의해 주세요."
    return {"status": status, "detail": detail}


def call_in_process_rag(payload: dict[str, Any], access: dict[str, Any]) -> dict:
    try:
        raw = execute_rag_chat(payload)
        return format_rag_api_result(raw, payload, access)
    except ValueError as exc:
        return build_error_result(payload, f"RAG request rejected: {safe_error(exc)}")
    except Exception as exc:
        return build_error_result(payload, f"RAG execution failed: {safe_error(exc)}")


def call_admin_layered_demo(
    payload: dict[str, Any],
    access: dict[str, Any],
    *,
    event_callback=None,
) -> dict:
    try:
        return get_admin_demo_service().run(payload, event_callback=event_callback)
    except ValueError as exc:
        return build_error_result(payload, f"Admin demo request rejected: {safe_error(exc)}")
    except Exception as exc:
        return build_error_result(payload, f"Admin demo execution failed: {safe_error(exc)}")


def build_error_result(payload: dict[str, Any], message: str) -> dict:
    return {
        "request_id": "REQ-RAG-ERROR",
        "guard_status": "ERROR",
        "answer_guard_status": "ERROR",
        "blocked": True,
        "answer": message,
        "sources": {"tables": [], "documents": []},
        "checks": {
            "rbac_enabled": payload.get("rbac_enabled", True),
            "pre_check": "ERROR",
            "post_check": "ERROR",
        },
        "raw": {},
    }


def normalize_api_sql_log(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": raw.get("request_id"),
        "query_time": (raw.get("raw") or {}).get("query_time") if isinstance(raw.get("raw"), dict) else None,
        "generated_sql": raw.get("generated_sql"),
        "row_count_returned": raw.get("row_count"),
        "columns": raw.get("columns") or [],
    }


def databricks_api_request(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    token = os.getenv("DATABRICKS_TOKEN", "").strip()
    if not host or not token:
        raise RuntimeError("DATABRICKS_HOST and DATABRICKS_TOKEN are required.")

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{host}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Databricks API HTTP {exc.code}: {detail}") from exc


def parse_notebook_result(raw_result: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_result)
    except json.JSONDecodeError:
        parsed = {"answer": raw_result}
    return parsed if isinstance(parsed, dict) else {"answer": str(parsed)}


def get_notebook_task_run_id(run_state: dict[str, Any], fallback_run_id: int) -> int:
    tasks = run_state.get("tasks") or []
    if not tasks:
        return fallback_run_id

    for task in tasks:
        if task.get("notebook_task") is not None and task.get("run_id") is not None:
            return int(task["run_id"])

    for task in tasks:
        if task.get("run_id") is not None:
            return int(task["run_id"])

    return fallback_run_id


def get_databricks_run_output_message(run_id: int) -> str:
    try:
        output = databricks_api_request(f"/api/2.1/jobs/runs/get-output?run_id={run_id}")
    except Exception as exc:
        return f"Could not retrieve notebook output: {exc}"

    notebook_output = output.get("notebook_output") or {}
    parts = [
        output.get("error"),
        output.get("error_trace"),
        notebook_output.get("result"),
        notebook_output.get("truncated"),
    ]
    message = "\n".join(str(part) for part in parts if part)
    return message[:4000] if message else "No notebook output was returned."


def call_databricks_job_rag(payload: dict[str, Any], access: dict[str, Any]) -> dict | None:
    job_id = os.getenv("DATABRICKS_JOB_ID", "").strip()
    if not job_id:
        return None

    encoded_question = base64.b64encode(payload["query"].encode("utf-8")).decode("ascii")
    body = {
        "job_id": int(job_id),
        "notebook_params": {
            "question": "",
            "question_b64": encoded_question,
            "question_encoding": "base64_utf8",
            "role_id": payload["role_id"],
            "rbac_enabled": "ON" if payload.get("rbac_enabled", True) else "OFF",
            "post_check": "ON" if payload.get("post_check_enabled", True) else "OFF",
        },
    }

    try:
        run_now = databricks_api_request("/api/2.1/jobs/run-now", method="POST", body=body)
        run_id = run_now["run_id"]
        output_run_id = run_id
        max_wait = int(os.getenv("DATABRICKS_JOB_TIMEOUT_SECONDS", "180"))
        started = time.time()

        while True:
            run_state = databricks_api_request(f"/api/2.1/jobs/runs/get?run_id={run_id}")
            output_run_id = get_notebook_task_run_id(run_state, run_id)
            state = run_state.get("state", {})
            life_cycle = state.get("life_cycle_state")
            result_state = state.get("result_state")

            if life_cycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
                if result_state != "SUCCESS":
                    message = state.get("state_message") or result_state or life_cycle
                    output_message = get_databricks_run_output_message(output_run_id)
                    raise RuntimeError(f"Databricks job failed: {message}\n{output_message}")
                break

            if time.time() - started > max_wait:
                raise TimeoutError(f"Databricks job timed out after {max_wait}s")
            time.sleep(3)

        output = databricks_api_request(f"/api/2.1/jobs/runs/get-output?run_id={output_run_id}")
        notebook_output = output.get("notebook_output") or {}
        raw_result = notebook_output.get("result") or ""
        parsed = parse_notebook_result(raw_result)
    except Exception as exc:
        return {
            "request_id": "REQ-DATABRICKS-JOB-ERROR",
            "guard_status": "ERROR",
            "answer_guard_status": "ERROR",
            "blocked": True,
            "answer": f"Databricks job connection failed: {exc}",
            "sources": {"tables": [], "documents": []},
            "checks": {
                "rbac_enabled": payload.get("rbac_enabled", True),
                "pre_check": "ERROR",
                "post_check": "ERROR",
            },
        }

    role_id = payload["role_id"]
    clearance = access["default_clearance"]
    sources = {"tables": [], "documents": []} if parsed.get("blocked") else normalize_sources(parsed, access["tables"], clearance, role_id)
    return {
        "request_id": parsed.get("request_id") or f"RUN-{run_id}",
        "guard_status": parsed.get("guard_status") or parsed.get("status") or "PASS",
        "answer_guard_status": parsed.get("answer_guard_status") or parsed.get("post_check") or "PASS",
        "blocked": bool(parsed.get("blocked", False)),
        "answer": parsed.get("answer") or parsed.get("response") or parsed.get("summary") or str(parsed),
        "sources": sources,
        "checks": parsed.get("checks") or build_check_result(
            payload.get("rbac_enabled", True),
            payload.get("pre_check_enabled", True),
            payload.get("post_check_enabled", True),
        ),
        "sql_log": parsed.get("sql_log") or parsed.get("log") or {},
        "raw": parsed.get("raw") if isinstance(parsed.get("raw"), dict) else parsed,
    }


def build_mock_answer(payload: dict[str, Any], access: dict[str, Any], mode: str) -> dict:
    return {
        "request_id": "REQ-NO-BACKEND",
        "guard_status": "ERROR",
        "answer_guard_status": "ERROR",
        "blocked": True,
        "checks": {
            "rbac_enabled": payload.get("rbac_enabled", True),
            "pre_check": "ERROR",
            "post_check": "ERROR",
        },
        "answer": "RAG backend is not configured. Set DATABRICKS_JOB_ID or RAG_API_URL before running the UI.",
        "sources": {"tables": [], "documents": []},
    }


def extract_sql_log(result: dict[str, Any], payload: dict[str, Any], access: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    sql_log = result.get("sql_log") if isinstance(result.get("sql_log"), dict) else {}
    raw_sql_log = raw.get("sql_log") if isinstance(raw.get("sql_log"), dict) else {}
    log = {**raw_sql_log, **sql_log}
    sources = result.get("sources") or {}
    tables = sources.get("tables") or []
    raw_table_access = raw.get("table_access") if isinstance(raw.get("table_access"), list) else []

    if not tables and raw_table_access:
        tables = [
            str(item.get("table"))
            for item in raw_table_access
            if isinstance(item, dict) and item.get("table")
        ]

    return {
        "request_id": result.get("request_id") or log.get("request_id") or raw.get("request_id") or "REQ-LIVE",
        "query_time": str(log.get("query_time") or raw.get("query_time") or datetime.now(ZoneInfo("Asia/Seoul")).isoformat()),
        "table_name": log.get("table_name") or ", ".join(tables) or "-",
        "row_count": log.get("row_count") or log.get("row_count_returned") or raw.get("row_count_returned") or 0,
        "column_count": log.get("column_count") or len(log.get("columns") or raw.get("columns_returned") or []),
        "columns": log.get("columns") or raw.get("columns_returned") or [],
        "actor": payload.get("role_id") or raw.get("role") or "-",
        "status": result.get("guard_status") or raw.get("status") or "UNKNOWN",
        "sql": log.get("sql") or log.get("generated_sql") or raw.get("sql") or "",
        "blocked": bool(result.get("blocked", False)),
        "department": payload.get("department_name") or access["department"],
        "clearance": payload.get("security_clearance") or access["default_clearance"],
    }


def remember_live_result(result: dict[str, Any], payload: dict[str, Any], access: dict[str, Any]) -> None:
    RECENT_RESPONSES.insert(0, result)
    del RECENT_RESPONSES[50:]
    RECENT_SQL_LOGS.insert(0, extract_sql_log(result, payload, access))
    del RECENT_SQL_LOGS[100:]


def build_ui_response(
    result: dict[str, Any],
    payload: dict[str, Any],
    access: dict[str, Any],
    mode: str,
    *,
    backend: str = "in_process_rag",
) -> dict:
    response = {
        **result,
        "endpoint": payload.get("endpoint", "/api/answer"),
        "query": payload["query"],
        "mode": mode,
        "role_id": payload["role_id"],
        "role_name": access["role_name"],
        "department_name": payload.get("department_name") or access["department"],
        "security_clearance": payload.get("security_clearance") or access["default_clearance"],
        "effective_identity": {
            "employee_id": "E20260001",
            "role_id": payload["role_id"],
            "department_name": payload.get("department_name") or access["department"],
            "security_clearance": payload.get("security_clearance") or access["default_clearance"],
        },
        "backend": backend,
        "security_mode": payload.get("security_mode") or "default",
    }
    return response


def log_ui_response(response: dict[str, Any], mode: str, backend: str) -> None:
    sources = response.get("sources") or {}
    raw = response.get("raw") if isinstance(response.get("raw"), dict) else {}
    raw_table_access = raw.get("table_access") if isinstance(raw.get("table_access"), list) else []
    print(
        "[RAG_UI_LOG]",
        json.dumps(
            {
                "mode": mode,
                "request_id": response.get("request_id"),
                "role_id": response.get("role_id"),
                "backend": backend,
                "guard_status": response.get("guard_status"),
                "blocked": response.get("blocked"),
                "tables_count": len(sources.get("tables") or []),
                "documents_count": len(sources.get("documents") or []),
                "raw_table_access_count": len(raw_table_access),
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def answer_payload(payload: dict[str, Any], mode: str) -> dict:
    payload = secure_request_payload(payload, mode)
    access = ROLE_ACCESS.get(payload["role_id"], ROLE_ACCESS["GENERAL_EMPLOYEE"])
    backend = "admin_layered_demo" if mode == "admin_simulation" else "in_process_rag"
    result = (
        call_admin_layered_demo(payload, access)
        if mode == "admin_simulation"
        else call_in_process_rag(payload, access)
    )
    response = build_ui_response(result, payload, access, mode, backend=backend)
    log_ui_response(response, mode, backend)
    remember_live_result(response, payload, access)
    return redact_public_response(response) if mode == "user" else response


def payload_to_dict(payload: BaseModel) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def sse_event(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "event": event,
        "data": json.dumps(payload, ensure_ascii=False, default=str),
    }


def native_stream_response(payload: RagChatRequest) -> EventSourceResponse:
    secured_payload = secure_native_request_payload(payload)

    async def event_generator():
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: str, event_payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, event_payload))

        def run_chat() -> dict[str, Any]:
            return get_rag_service().chat(
                question=secured_payload["question"],
                role_id=secured_payload["role_id"],
                mode=secured_payload["mode"],
                rbac_enabled=secured_payload["rbac_enabled"],
                post_check=secured_payload["post_check"],
                top_k=secured_payload["top_k"],
                event_callback=emit,
            )

        task = asyncio.create_task(asyncio.to_thread(run_chat))
        try:
            while not task.done() or not queue.empty():
                try:
                    event, event_payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield sse_event(event, event_payload)
                except asyncio.TimeoutError:
                    continue
            result = await task
            yield sse_event("final", result)
        except ValueError as exc:
            yield sse_event("error", {"status": 400, "detail": safe_error(exc)})
        except Exception as exc:
            yield sse_event("error", {"status": 502, "detail": safe_error(exc)})

    return EventSourceResponse(event_generator())


def ui_stream_response(payload: dict[str, Any], mode: str) -> EventSourceResponse:
    payload = secure_request_payload(payload, mode)

    async def event_generator():
        access = ROLE_ACCESS.get(payload["role_id"], ROLE_ACCESS["GENERAL_EMPLOYEE"])
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: str, event_payload: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, event_payload))

        def run_chat() -> dict[str, Any]:
            result = (
                call_admin_layered_demo(payload, access, event_callback=emit)
                if mode == "admin_simulation"
                else format_rag_api_result(
                    execute_rag_chat(payload, event_callback=emit),
                    payload,
                    access,
                )
            )
            return build_ui_response(result, payload, access, mode)

        task = asyncio.create_task(asyncio.to_thread(run_chat))
        try:
            while not task.done() or not queue.empty():
                try:
                    event, event_payload = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield sse_event(event, event_payload)
                except asyncio.TimeoutError:
                    continue
            response = await task
            backend = "admin_layered_demo" if mode == "admin_simulation" else "in_process_rag"
            response["backend"] = backend
            log_ui_response(response, mode, backend)
            remember_live_result(response, payload, access)
            final_response = redact_public_response(response) if mode == "user" else response
            yield sse_event("final", final_response)
        except ValueError as exc:
            yield sse_event("error", stream_error_payload(exc, 400, mode))
        except Exception as exc:
            yield sse_event("error", stream_error_payload(exc, 502, mode))

    return EventSourceResponse(event_generator())


@app.get("/")
def public_ui():
    return FileResponse("app/templates/public.html")


@app.get("/admin-login")
def admin_login_ui():
    return FileResponse("app/templates/login.html")


@app.get("/admin")
@app.get("/ui")
def admin_ui():
    return FileResponse("app/templates/admin.html")


@app.get("/health")
@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "databricks_configured": databricks_configured(),
        "build": build_info(),
    }


@app.get("/api/backend/status")
def backend_status():
    return {
        "backend": "in_process_rag",
        "rag_api_url_configured": False,
        "databricks_job_configured": False,
        "databricks_host_configured": bool(os.getenv("DATABRICKS_HOST", "").strip()),
        "databricks_sql_configured": databricks_configured(),
        "build": build_info(),
    }


@app.get("/api/admin/debug/rbac/{role_id}")
def debug_rbac(role_id: str):
    try:
        service = get_rag_service()
        active_role = validate_role_id(role_id, service.role_ids)
        domains = get_allowed_domains(
            service.sql_client,
            active_role,
            service.role_ids,
            service.settings.catalog,
        )
        domain_allowed_tables = service.mappings.get_allowed_tables(domains)
        role_table_access = get_role_table_access(
            service.sql_client,
            active_role,
            service.role_ids,
            service.settings.catalog,
        )
        role_allowed_tables = role_table_access.tables
        effective_allowed_tables = role_allowed_tables
        domain_overlap_tables = role_allowed_tables.intersection(domain_allowed_tables)
        salary_probe = build_salary_subquery_probe(service, effective_allowed_tables)
        payroll_table = f"{service.settings.catalog}.silver.hr_payroll_summary"
        return {
            "build": build_info(),
            "role_id": active_role,
            "catalog": service.settings.catalog,
            "domains": domains,
            "role_table_source": role_table_access.source,
            "role_table_fallback_used": role_table_access.fallback_used,
            "role_table_warnings": role_table_access.warnings,
            "role_tables_missing_in_catalog": role_table_access.missing_tables,
            "role_allowed_tables": sorted(role_allowed_tables),
            "domain_allowed_tables": sorted(domain_allowed_tables),
            "role_tables_outside_domain_mapping": sorted(role_allowed_tables - domain_allowed_tables),
            "domain_overlap_tables": sorted(domain_overlap_tables),
            "effective_allowed_tables": sorted(effective_allowed_tables),
            "counts": {
                "role_allowed_tables": len(role_allowed_tables),
                "domain_allowed_tables": len(domain_allowed_tables),
                "domain_overlap_tables": len(domain_overlap_tables),
                "effective_allowed_tables": len(effective_allowed_tables),
            },
            "salary_subquery_probe": salary_probe,
            "sensitive_table_probe": build_sensitive_table_probe(
                service,
                active_role,
                [payroll_table],
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=safe_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_error(exc)) from exc


@app.post("/api/admin/login")
def admin_login(payload: LoginRequest):
    ok = payload.username == "admin" and payload.password == "admin"
    return {"ok": ok, "redirect": "/admin", "message": "Login success" if ok else "Invalid username or password"}


@app.post("/api/chat")
def chat(payload: ChatRequest):
    return answer_payload(payload_to_dict(payload), "user")


@app.post("/api/chat/stream")
async def chat_stream_ui(payload: ChatRequest) -> EventSourceResponse:
    return ui_stream_response(payload_to_dict(payload), "user")


@app.post("/api/admin/simulate")
def simulate(payload: SimulateRequest):
    return answer_payload(payload_to_dict(payload), "admin_simulation")


@app.post("/api/admin/simulate/stream")
async def simulate_stream(payload: SimulateRequest) -> EventSourceResponse:
    return ui_stream_response(payload_to_dict(payload), "admin_simulation")


@app.post("/v1/chat")
def rag_chat(payload: RagChatRequest) -> dict[str, object]:
    secured_payload = secure_native_request_payload(payload)
    try:
        return get_rag_service().chat(
            question=secured_payload["question"],
            role_id=secured_payload["role_id"],
            mode=secured_payload["mode"],
            rbac_enabled=secured_payload["rbac_enabled"],
            post_check=secured_payload["post_check"],
            top_k=secured_payload["top_k"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=safe_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=safe_error(exc)) from exc


@app.post("/v1/chat/stream")
async def rag_chat_stream(payload: RagChatRequest) -> EventSourceResponse:
    return native_stream_response(payload)


@app.get("/api/admin/dashboard/llm")
def llm_dashboard():
    total = len(RECENT_RESPONSES)
    blocked = sum(1 for item in RECENT_RESPONSES if item.get("blocked"))
    failed = sum(1 for item in RECENT_RESPONSES if item.get("guard_status") == "ERROR")
    completed = sum(1 for item in RECENT_RESPONSES if not item.get("blocked") and item.get("guard_status") != "ERROR")
    no_evidence = sum(1 for item in RECENT_RESPONSES if not (item.get("sources") or {}).get("tables"))
    guard_pass = f"{round((completed / total) * 100, 1)}%" if total else "0%"
    return {
        "total_requests": total,
        "completed_requests": completed,
        "blocked_requests": blocked,
        "failed_requests": failed,
        "no_evidence_requests": no_evidence,
        "guard_pass": guard_pass,
        "registered_roles": len(ROLE_ACCESS),
    }


@app.get("/api/admin/dashboard/database")
def database_dashboard():
    queried_tables = {
        table
        for item in RECENT_RESPONSES
        for table in (item.get("sources") or {}).get("tables", [])
    }
    document_count = sum(len((item.get("sources") or {}).get("documents", [])) for item in RECENT_RESPONSES)
    blocked_logs = sum(1 for item in RECENT_SQL_LOGS if item.get("blocked"))
    top_table = "-"
    if queried_tables:
        top_table = max(
            queried_tables,
            key=lambda table: sum(1 for log in RECENT_SQL_LOGS if table in str(log.get("table_name", ""))),
        )
    return {
        "queried_tables": len(queried_tables),
        "document_citations": document_count,
        "access_policies": len(ROLE_ACCESS),
        "blocked_access_logs": blocked_logs,
        "top_table": top_table,
        "logged_sql_queries": len(RECENT_SQL_LOGS),
    }


@app.get("/api/admin/roles")
def roles():
    return [
        {
            "role_id": role_id,
            "role_name": access["role_name"],
            "description": access["description"],
            "department": access["department"],
            "default_clearance": access["default_clearance"],
        }
        for role_id, access in ROLE_ACCESS.items()
    ]


@app.get("/api/admin/roles/{role_id}/access")
def get_role_access(role_id: str):
    access = ROLE_ACCESS.get(role_id, ROLE_ACCESS["GENERAL_EMPLOYEE"])
    role_logs = [log for log in RECENT_SQL_LOGS if log.get("actor") == role_id]
    role_responses = [item for item in RECENT_RESPONSES if item.get("role_id") == role_id]
    requests = len(role_responses)
    blocked = sum(1 for item in role_responses if item.get("blocked"))
    failed = sum(1 for item in role_responses if item.get("guard_status") == "ERROR")
    completed = sum(1 for item in role_responses if not item.get("blocked") and item.get("guard_status") != "ERROR")
    top_tables = {}
    for log in role_logs:
        for table in str(log.get("table_name", "")).split(", "):
            if table and table != "-":
                top_tables[table] = top_tables.get(table, 0) + 1
    blocked_attempts = [
        {"table": log.get("table_name") or "-", "count": 1, "reason": "RBAC/guard blocked"}
        for log in role_logs
        if log.get("blocked")
    ]
    return {
        "role_id": role_id,
        "role_name": access["role_name"],
        "description": access["description"],
        "department": access["department"],
        "default_clearance": access["default_clearance"],
        "systems": access["systems"],
        "domains": access["domains"],
        "tables": access["tables"],
        "usage": {
            "requests": requests,
            "completed": completed,
            "blocked": blocked,
            "failed": failed,
            "pre_check_blocked": sum(1 for item in role_responses if (item.get("checks") or {}).get("pre_check") == "BLOCKED"),
            "post_check_blocked": sum(1 for item in role_responses if (item.get("checks") or {}).get("post_check") == "BLOCKED"),
            "no_evidence": sum(1 for item in role_responses if not (item.get("sources") or {}).get("tables")),
            "guard_pass_rate": f"{round((completed / requests) * 100, 1)}%" if requests else "0%",
        },
        "top_tables": [
            {"table": table, "count": count}
            for table, count in sorted(top_tables.items(), key=lambda item: item[1], reverse=True)[:5]
        ],
        "top_documents": [],
        "blocked_attempts": blocked_attempts,
    }


def parse_log_time(value: Any) -> datetime | None:
    if not value:
        return None

    raw = str(value).strip()
    normalized = raw.replace("Z", "+00:00")
    if "T" not in normalized and " " in normalized:
        normalized = normalized.replace(" ", "T", 1)

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(ZoneInfo("Asia/Seoul"))


@app.get("/api/admin/sql-logs")
def sql_logs(
    page: int = 1,
    page_size: int = 15,
    days: int = 7,
    role: str = "",
    status: str = "",
    table: str = "",
    date_from: str = "",
    date_to: str = "",
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 50)
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    filtered = []

    for log in RECENT_SQL_LOGS:
        log_time = parse_log_time(log.get("query_time"))
        if days and not date_from and not date_to:
            if not log_time or (now_kst - log_time).days >= days:
                continue

        if date_from and (not log_time or log_time.date().isoformat() < date_from):
            continue
        if date_to and (not log_time or log_time.date().isoformat() > date_to):
            continue
        if role and str(log.get("actor", "")).upper() != role.upper():
            continue
        if status and str(log.get("status", "")).upper() != status.upper():
            continue
        if table and table.lower() not in str(log.get("table_name", "")).lower():
            continue

        filtered.append(log)

    total = len(filtered)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "logs": filtered[start:end],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "filters": {
            "days": days,
            "role": role,
            "status": status,
            "table": table,
            "date_from": date_from,
            "date_to": date_to,
        },
        "retention_note": "Default view shows recent 7 days. Persist production logs in a Databricks Delta audit table.",
    }
