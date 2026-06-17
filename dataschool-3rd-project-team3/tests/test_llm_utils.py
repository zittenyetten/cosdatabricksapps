from rbac_rag.llm import DatabricksLLM


def test_extract_sql_from_sql_fence() -> None:
    assert (
        DatabricksLLM.extract_sql(
            "```sql\nSELECT * FROM cos_adb.silver.events\n```"
        )
        == "SELECT * FROM cos_adb.silver.events"
    )


def test_extract_sql_from_uppercase_sql_fence() -> None:
    assert (
        DatabricksLLM.extract_sql(
            "```SQL\nSELECT * FROM cos_adb.silver.events\n```"
        )
        == "SELECT * FROM cos_adb.silver.events"
    )


def test_extract_sql_from_plain_text_prefix() -> None:
    assert (
        DatabricksLLM.extract_sql(
            "Here is the query:\nSELECT * FROM cos_adb.silver.events"
        )
        == "SELECT * FROM cos_adb.silver.events"
    )

