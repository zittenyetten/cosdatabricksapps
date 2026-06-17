from rbac_rag.env_check import check_env_file, mask_env_value


def test_mask_env_value_masks_token() -> None:
    assert mask_env_value("DATABRICKS_TOKEN", "sample-token") == "<set:12 chars>"
    assert mask_env_value("DATABRICKS_CLIENT_SECRET", "secret-value") == "<set:12 chars>"


def test_mask_env_value_leaves_non_secret_visible() -> None:
    assert (
        mask_env_value("DATABRICKS_SERVER_HOSTNAME", "adb.example.databricks.net")
        == "adb.example.databricks.net"
    )


def test_check_env_file_detects_placeholders(tmp_path) -> None:
    env_file = tmp_path / ".env"
    token_key = "DATABRICKS_TOKEN"
    env_file.write_text(
        "\n".join(
            [
                "DATABRICKS_HOST=https://adb.example.databricks.net",
                "DATABRICKS_SERVER_HOSTNAME=adb.example.databricks.net",
                "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>",
                f"{token_key}=sample-token",
            ]
        ),
        encoding="utf-8",
    )

    rows, ok = check_env_file(env_file)

    assert ok is False
    assert rows[2]["placeholder"] is True
    assert rows[6]["value"] == "<set:12 chars>"


def test_check_env_file_accepts_oauth_without_pat(tmp_path) -> None:
    env_file = tmp_path / ".env"
    secret_key = "DATABRICKS_CLIENT_SECRET"
    env_file.write_text(
        "\n".join(
            [
                "DATABRICKS_HOST=https://adb.example.databricks.net",
                "DATABRICKS_SERVER_HOSTNAME=adb.example.databricks.net",
                "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc",
                "DATABRICKS_CLIENT_ID=client-id",
                f"{secret_key}=client-secret",
            ]
        ),
        encoding="utf-8",
    )

    rows, ok = check_env_file(env_file)

    assert ok is True
    secret_row = next(row for row in rows if row["key"] == "DATABRICKS_CLIENT_SECRET")
    assert secret_row["value"] == "<set:13 chars>"


def test_check_env_file_accepts_warehouse_id_without_http_path(tmp_path) -> None:
    env_file = tmp_path / ".env"
    secret_key = "DATABRICKS_CLIENT_SECRET"
    env_file.write_text(
        "\n".join(
            [
                "DATABRICKS_HOST=https://adb.example.databricks.net",
                "DATABRICKS_SERVER_HOSTNAME=adb.example.databricks.net",
                "DATABRICKS_WAREHOUSE_ID=abc",
                "DATABRICKS_CLIENT_ID=client-id",
                f"{secret_key}=client-secret",
            ]
        ),
        encoding="utf-8",
    )

    _, ok = check_env_file(env_file)

    assert ok is True
