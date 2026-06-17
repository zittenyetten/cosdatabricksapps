from dataclasses import dataclass
from typing import Any


@dataclass
class TableMappings:
    catalog: str
    table_rows: list[Any]
    context_rows: list[Any]
    domain_to_tables: dict[str, set[str]]
    table_id_to_fqn: dict[str, str]

    @classmethod
    def build(cls, spark: Any, catalog: str) -> "TableMappings":
        table_rows = spark.sql(
            f"""
            SELECT table_schema, table_name, CONCAT('{catalog}.', table_schema, '.', table_name) AS fqn
            FROM {catalog}.information_schema.tables
            WHERE table_schema != 'information_schema'
            """
        ).collect()
        context_rows = spark.sql(
            f"SELECT table_id, layer, domain FROM {catalog}.search.llm_table_context"
        ).collect()

        domain_to_tables: dict[str, set[str]] = {}
        table_id_to_fqn: dict[str, str] = {}

        for ctx in context_rows:
            last_part = ctx.table_id.split("__")[-1]
            for table in table_rows:
                if last_part == table.table_name or table.table_name.endswith(last_part):
                    domain_to_tables.setdefault(ctx.domain, set()).add(table.fqn)
                    table_id_to_fqn[ctx.table_id] = table.fqn

        for domain in domain_to_tables:
            domain_to_tables[domain].add(f"{catalog}.silver.events")

        return cls(
            catalog=catalog,
            table_rows=table_rows,
            context_rows=context_rows,
            domain_to_tables=domain_to_tables,
            table_id_to_fqn=table_id_to_fqn,
        )

    def get_allowed_table_list(self, domains: list[str]) -> str:
        return "\n".join(f"  - {table}" for table in sorted(self.get_allowed_tables(domains)))

    def get_allowed_tables(self, domains: list[str]) -> set[str]:
        tables: set[str] = set()
        for domain in domains:
            tables.update(self.domain_to_tables.get(domain, set()))
        tables.update(self.domain_to_tables.get("Master/Governance", set()))
        return tables

    def get_table_id_mapping_str(self, domains: list[str]) -> str:
        return "\n".join(
            sorted(
                f"  {ctx.table_id} -> {self.table_id_to_fqn[ctx.table_id]}"
                for ctx in self.context_rows
                if ctx.domain in domains and ctx.table_id in self.table_id_to_fqn
            )
        )

    def get_all_table_list(self) -> str:
        return "\n".join(f"  - {row.fqn}" for row in self.table_rows)

    def get_all_tables(self) -> set[str]:
        return {row.fqn for row in self.table_rows}

    def get_all_domains(self) -> list[str]:
        return sorted(self.domain_to_tables.keys())
