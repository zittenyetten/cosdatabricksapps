from rbac_rag.sql_client import DatabricksSqlConfig


def test_config_builds_http_path_from_warehouse_id(monkeypatch) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://adb.example.databricks.net")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "client-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("DATABRICKS_SERVER_HOSTNAME", raising=False)
    monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    config = DatabricksSqlConfig.from_env()

    assert config.server_hostname == "adb.example.databricks.net"
    assert config.http_path == "/sql/1.0/warehouses/abc123"
    assert config.use_oauth is True
