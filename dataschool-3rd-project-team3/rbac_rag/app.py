from dataclasses import dataclass
from typing import Any

from .engine import RagEngine
from .llm import DatabricksLLM
from .mappings import TableMappings
from .rbac import (
    ensure_widgets,
    get_allowed_domains,
    list_role_ids,
    resolve_selected_role,
    validate_role_id,
)
from .router import QueryRouter
from .settings import RagSettings, RuntimeFlags


@dataclass
class RagApp:
    settings: RagSettings
    runtime: RuntimeFlags
    rag_engine: RagEngine
    router: QueryRouter


def create_app(
    *,
    spark: Any,
    dbutils: Any,
    settings: RagSettings | None = None,
) -> RagApp:
    settings = settings or RagSettings()

    role_ids = list_role_ids(spark, settings.catalog)
    ensure_widgets(dbutils, role_ids)

    selected_role = validate_role_id(resolve_selected_role(dbutils), role_ids)
    rbac_enabled = dbutils.widgets.get("rbac_enabled") == "ON"
    post_check_enabled = dbutils.widgets.get("post_check") == "ON"
    allowed_domains = (
        get_allowed_domains(spark, selected_role, role_ids, settings.catalog) if rbac_enabled else []
    )

    runtime = RuntimeFlags(
        selected_role=selected_role,
        rbac_enabled=rbac_enabled,
        post_check_enabled=post_check_enabled,
        allowed_domains=allowed_domains,
    )

    mappings = TableMappings.build(spark, settings.catalog)
    llm = DatabricksLLM(settings)
    rag_engine = RagEngine(
        spark=spark,
        llm=llm,
        settings=settings,
        mappings=mappings,
        selected_role=runtime.selected_role,
        rbac_enabled=runtime.rbac_enabled,
        post_check_enabled=runtime.post_check_enabled,
        allowed_domains=runtime.allowed_domains,
        valid_role_ids=role_ids,
        audit_logger=None,
        display_results=True,
    )
    router = QueryRouter(rag_engine=rag_engine, llm=llm)

    print("SELECTED_ROLE =", runtime.selected_role)
    print("ALLOWED_DOMAINS =", runtime.allowed_domains)
    print("[OK] route_query() 등록 완료 - /chat, /work, auto-intent 지원")

    return RagApp(settings=settings, runtime=runtime, rag_engine=rag_engine, router=router)
