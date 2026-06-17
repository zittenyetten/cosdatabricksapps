import sys
import types

from rbac_rag.sql_client import DatabricksSqlClient, DatabricksSqlConfig


class FakeCursor:
    description = [("value",)]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query, parameters=None):
        self.query = query
        self.parameters = parameters

    def fetchall(self):
        return [(1,)]


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def cursor(self):
        return FakeCursor()


def install_fake_databricks_sql(monkeypatch):
    captured = {}

    def connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    sql_module = types.SimpleNamespace(connect=connect)
    databricks_module = types.SimpleNamespace(sql=sql_module)
    monkeypatch.setitem(sys.modules, "databricks", databricks_module)
    monkeypatch.setitem(sys.modules, "databricks.sql", sql_module)
    return captured


def test_sql_client_uses_oauth_credentials_provider(monkeypatch) -> None:
    captured = install_fake_databricks_sql(monkeypatch)
    client = DatabricksSqlClient(
        DatabricksSqlConfig(
            server_hostname="adb.example.databricks.net",
            http_path="/sql/1.0/warehouses/abc",
            use_oauth=True,
        )
    )

    result = client.sql("SELECT 1")

    assert result.to_records() == [{"value": 1}]
    assert "credentials_provider" in captured
    assert "access_token" not in captured


def test_sql_client_uses_pat_fallback(monkeypatch) -> None:
    captured = install_fake_databricks_sql(monkeypatch)
    client = DatabricksSqlClient(
        DatabricksSqlConfig(
            server_hostname="adb.example.databricks.net",
            http_path="/sql/1.0/warehouses/abc",
            access_token="token",
        )
    )

    result = client.sql("SELECT 1")

    assert result.to_records() == [{"value": 1}]
    assert captured["access_token"] == "token"
    assert "credentials_provider" not in captured
