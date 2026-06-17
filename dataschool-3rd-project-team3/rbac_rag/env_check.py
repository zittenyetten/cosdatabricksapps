from pathlib import Path


REQUIRED_DATABRICKS_KEYS = [
    "DATABRICKS_HOST",
    "DATABRICKS_SERVER_HOSTNAME",
]

SQL_COMPUTE_GROUPS = [
    ("HTTP path", ["DATABRICKS_HTTP_PATH"]),
    ("SQL warehouse resource", ["DATABRICKS_WAREHOUSE_ID"]),
]

AUTH_GROUPS = [
    ("OAuth M2M", ["DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"]),
    ("PAT fallback", ["DATABRICKS_TOKEN"]),
]

SENSITIVE_KEY_PARTS = ("TOKEN", "SECRET", "PASSWORD", "KEY")


def read_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def mask_env_value(key: str, value: str) -> str:
    if not value:
        return ""
    if any(part in key.upper() for part in SENSITIVE_KEY_PARTS):
        return f"<set:{len(value)} chars>"
    return value


def check_env_file(path: str | Path) -> tuple[list[dict[str, str | bool]], bool]:
    values = read_env_file(path)
    rows: list[dict[str, str | bool]] = []
    ok = True

    for key in (
        REQUIRED_DATABRICKS_KEYS
        + [key for _, keys in SQL_COMPUTE_GROUPS for key in keys]
        + [key for _, keys in AUTH_GROUPS for key in keys]
    ):
        value = values.get(key, "")
        present = bool(value)
        placeholder = "<" in value or ">" in value
        valid = present and not placeholder
        rows.append(
            {
                "key": key,
                "present": present,
                "placeholder": placeholder,
                "valid": valid,
                "value": mask_env_value(key, value),
            }
        )

    required_ok = all(
        bool(values.get(key, "")) and "<" not in values.get(key, "") and ">" not in values.get(key, "")
        for key in REQUIRED_DATABRICKS_KEYS
    )
    sql_compute_ok = any(
        all(
            bool(values.get(key, ""))
            and "<" not in values.get(key, "")
            and ">" not in values.get(key, "")
            for key in keys
        )
        for _, keys in SQL_COMPUTE_GROUPS
    )
    auth_ok = any(
        all(
            bool(values.get(key, ""))
            and "<" not in values.get(key, "")
            and ">" not in values.get(key, "")
            for key in keys
        )
        for _, keys in AUTH_GROUPS
    )
    ok = required_ok and sql_compute_ok and auth_ok
    return rows, ok
