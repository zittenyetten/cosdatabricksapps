import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rbac_rag.mappings import TableMappings
from rbac_rag.rbac import ROLE_TABLE_POLICY_TABLE, get_allowed_domains, get_role_allowed_tables, list_role_ids
from rbac_rag.settings import RagSettings
from rbac_rag.sql_client import DatabricksSqlClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only cos_adb RBAC metadata audit.")
    parser.add_argument("--output", default="", help="Optional JSON output path.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    load_dotenv(project_root / ".env")
    load_dotenv(project_root / "dataschool-3rd-project-team3" / ".env")

    settings = RagSettings.from_env()
    client = DatabricksSqlClient()
    catalog = settings.catalog

    tables = rows(
        client,
        f"""
        SELECT table_schema, table_name, table_type,
               CONCAT('{catalog}.', table_schema, '.', table_name) AS fqn
        FROM {catalog}.information_schema.tables
        WHERE table_schema != 'information_schema'
        ORDER BY table_schema, table_name
        """,
    )
    catalog_tables = {row["fqn"] for row in tables}
    mappings = TableMappings.build(client, catalog)
    role_ids = list_role_ids(client, catalog)
    policy_rows_by_role, policy_warning = load_role_table_policy(client, catalog)

    role_audit = []
    for role_id in role_ids:
        domains = get_allowed_domains(client, role_id, role_ids, catalog)
        domain_tables = mappings.get_allowed_tables(domains)
        fallback_tables = get_role_allowed_tables(role_id, catalog)
        if policy_rows_by_role is not None and policy_rows_by_role.get(role_id):
            raw_role_tables = policy_rows_by_role[role_id]
            role_source = f"{catalog}.{ROLE_TABLE_POLICY_TABLE}"
            fallback_used = False
            warnings = []
        else:
            raw_role_tables = fallback_tables
            role_source = "fallback:FALLBACK_ROLE_ALLOWED_TABLES"
            fallback_used = True
            warnings = [policy_warning or "No active role_table_permissions rows; using repo fallback."]
        missing_role_tables = sorted(raw_role_tables - catalog_tables)
        role_tables = raw_role_tables.intersection(catalog_tables)
        role_audit.append(
            {
                "role_id": role_id,
                "domains": domains,
                "role_table_source": role_source,
                "fallback_used": fallback_used,
                "warnings": warnings,
                "fallback_missing_in_catalog": sorted(fallback_tables - catalog_tables),
                "role_tables_missing_in_catalog": missing_role_tables,
                "domain_table_count": len(domain_tables),
                "role_table_count": len(role_tables),
                "domain_overlap_count": len(role_tables.intersection(domain_tables)),
                "effective_tables": sorted(role_tables),
                "role_tables_outside_domain_mapping": sorted(role_tables - domain_tables),
            }
        )

    unmapped_context_ids = sorted(
        {row.table_id for row in mappings.context_rows} - set(mappings.table_id_to_fqn)
    )
    report = {
        "catalog": catalog,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "table_count": len(tables),
        "role_count": len(role_ids),
        "mapping_summary": {
            "context_rows": len(mappings.context_rows),
            "mapped_context_ids": len(mappings.table_id_to_fqn),
            "unmapped_context_count": len(unmapped_context_ids),
            "unmapped_context_ids": unmapped_context_ids,
        },
        "roles_with_no_effective_tables": [
            row["role_id"] for row in role_audit if row["role_table_count"] == 0
        ],
        "roles_with_missing_fallback_tables": [
            {
                "role_id": row["role_id"],
                "missing": row["fallback_missing_in_catalog"],
            }
            for row in role_audit
            if row["fallback_missing_in_catalog"]
        ],
        "roles_with_tables_outside_domain_mapping": [
            {
                "role_id": row["role_id"],
                "outside_domain_mapping": row["role_tables_outside_domain_mapping"],
            }
            for row in role_audit
            if row["role_tables_outside_domain_mapping"]
        ],
        "role_audit": role_audit,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(jsonable(report), ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "catalog": report["catalog"],
        "table_count": report["table_count"],
        "role_count": report["role_count"],
        "unmapped_context_count": report["mapping_summary"]["unmapped_context_count"],
        "roles_with_no_effective_tables": report["roles_with_no_effective_tables"],
        "roles_with_missing_fallback_tables": report["roles_with_missing_fallback_tables"],
    }
    print(json.dumps(jsonable(summary), ensure_ascii=False, indent=2))


def rows(client: DatabricksSqlClient, statement: str) -> list[dict[str, Any]]:
    return [row.as_dict() for row in client.sql(statement).collect()]


def load_role_table_policy(
    client: DatabricksSqlClient,
    catalog: str,
) -> tuple[dict[str, set[str]] | None, str | None]:
    try:
        policy_rows = rows(
            client,
            f"""
            SELECT role_id, table_fqn
            FROM {catalog}.{ROLE_TABLE_POLICY_TABLE}
            WHERE COALESCE(is_active, true) = true
            ORDER BY role_id, table_fqn
            """,
        )
    except Exception as error:
        return None, f"{catalog}.{ROLE_TABLE_POLICY_TABLE} unavailable ({error.__class__.__name__})."

    grouped: dict[str, set[str]] = {}
    for row in policy_rows:
        role_id = str(row.get("role_id") or "").strip()
        table_fqn = str(row.get("table_fqn") or "").strip()
        if role_id and table_fqn:
            grouped.setdefault(role_id, set()).add(table_fqn)
    return grouped, None


def jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if hasattr(value, "tolist"):
        return jsonable(value.tolist())
    return value


if __name__ == "__main__":
    main()
