from types import SimpleNamespace

from rbac_rag.mappings import TableMappings


class FakeSql:
    def sql(self, statement):
        if "information_schema.tables" in statement:
            return FakeResult(
                [
                    {
                        "table_schema": "silver",
                        "table_name": "cs_customer_inquiries",
                        "fqn": "cos_adb.silver.cs_customer_inquiries",
                    },
                    {
                        "table_schema": "silver",
                        "table_name": "voc_review_voc_insights",
                        "fqn": "cos_adb.silver.voc_review_voc_insights",
                    },
                    {
                        "table_schema": "silver",
                        "table_name": "events",
                        "fqn": "cos_adb.silver.events",
                    },
                ]
            )
        if "search.llm_table_context" in statement:
            return FakeResult(
                [
                    {
                        "table_id": "silver__voc_review_voc_insights",
                        "layer": "silver",
                        "domain": "Customer Service",
                    },
                    {
                        "table_id": "silver__cs_customer_inquiries",
                        "layer": "silver",
                        "domain": "Customer Service",
                    },
                ]
            )
        if "information_schema.columns" in statement:
            return FakeResult(
                [
                    {
                        "table_schema": "silver",
                        "table_name": "voc_review_voc_insights",
                        "column_name": "record_id",
                        "ordinal_position": 1,
                    },
                    {
                        "table_schema": "silver",
                        "table_name": "voc_review_voc_insights",
                        "column_name": "status",
                        "ordinal_position": 2,
                    },
                    {
                        "table_schema": "silver",
                        "table_name": "cs_customer_inquiries",
                        "column_name": "inquiry_id",
                        "ordinal_position": 1,
                    },
                ]
            )
        raise AssertionError(f"unexpected query: {statement}")


class FakeColumnErrorSql(FakeSql):
    def sql(self, statement):
        if "information_schema.columns" in statement:
            raise RuntimeError("column metadata unavailable")
        return super().sql(statement)


class FakeResult:
    def __init__(self, rows):
        self.rows = [SimpleNamespace(**row) for row in rows]

    def collect(self):
        return self.rows


def test_table_mappings_include_column_reference_in_allowed_tables() -> None:
    mappings = TableMappings.build(FakeSql(), "cos_adb")

    table_list = mappings.get_allowed_table_list(["Customer Service"])

    assert "cos_adb.silver.voc_review_voc_insights (columns: record_id, status)" in table_list
    assert "cos_adb.silver.cs_customer_inquiries (columns: inquiry_id)" in table_list


def test_table_mappings_fall_back_when_columns_are_unknown() -> None:
    mappings = TableMappings.build(FakeSql(), "cos_adb")

    table_list = mappings.get_allowed_table_list(["Customer Service"])

    assert "cos_adb.silver.events" in table_list


def test_table_mappings_builds_when_column_metadata_is_unavailable() -> None:
    mappings = TableMappings.build(FakeColumnErrorSql(), "cos_adb")

    table_list = mappings.get_allowed_table_list(["Customer Service"])

    assert "cos_adb.silver.voc_review_voc_insights" in table_list
    assert "columns:" not in table_list
