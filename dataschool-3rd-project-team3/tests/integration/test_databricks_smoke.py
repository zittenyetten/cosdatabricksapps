import os

import pytest

from rbac_rag.llm import DatabricksLLM
from rbac_rag.rbac import list_role_ids
from rbac_rag.settings import RagSettings
from rbac_rag.sql_client import DatabricksSqlClient


pytestmark = pytest.mark.databricks


def _require_databricks() -> None:
    if os.getenv("RUN_DATABRICKS_TESTS") != "1":
        pytest.skip("Set RUN_DATABRICKS_TESTS=1 to run live Databricks smoke tests")


def test_sql_warehouse_smoke() -> None:
    _require_databricks()
    client = DatabricksSqlClient()

    rows = client.sql("SELECT 1 AS ok").collect()

    assert rows[0].ok == 1


def test_catalog_smoke() -> None:
    _require_databricks()
    settings = RagSettings.from_env()
    client = DatabricksSqlClient()

    assert list_role_ids(client, settings.catalog)
    assert client.sql(
        f"SELECT table_id, layer, domain FROM {settings.catalog}.search.llm_table_context LIMIT 1"
    ).collect()


def test_model_and_search_smoke() -> None:
    _require_databricks()
    settings = RagSettings.from_env()
    llm = DatabricksLLM(settings)

    assert llm.llm_call("Respond with OK only.", "OK?", max_tokens=8).strip()
    assert llm.search_metadata("품질", top_k=1, vs_index_name=settings.vs_index_name) is not None

