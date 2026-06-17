import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


PARAMETER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


class SqlRow:
    def __init__(self, values: dict[str, Any]):
        self._values = values

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)


class LightweightTable:
    def __init__(self, rows: list[dict[str, Any]], columns: list[str]):
        self._rows = rows
        self.columns = columns

    def __len__(self) -> int:
        return len(self._rows)

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        if orient != "records":
            raise ValueError("Only orient='records' is supported")
        return [dict(row) for row in self._rows]

    def to_string(self, index: bool = False) -> str:
        if not self._rows:
            return ""
        widths = {
            column: max(len(str(column)), *(len(str(row.get(column, ""))) for row in self._rows))
            for column in self.columns
        }
        header = "  ".join(str(column).ljust(widths[column]) for column in self.columns)
        separator = "  ".join("-" * widths[column] for column in self.columns)
        lines = [header, separator]
        for idx, row in enumerate(self._rows):
            prefix = f"{idx} " if index else ""
            lines.append(
                prefix
                + "  ".join(str(row.get(column, "")).ljust(widths[column]) for column in self.columns)
            )
        return "\n".join(lines)


class SqlResult:
    def __init__(self, rows: list[dict[str, Any]], columns: list[str]):
        self._rows = rows
        self.columns = columns

    def collect(self) -> list[SqlRow]:
        return [SqlRow(row) for row in self._rows]

    def limit(self, count: int) -> "SqlResult":
        return SqlResult(self._rows[:count], self.columns)

    def toPandas(self) -> LightweightTable:
        return LightweightTable(self._rows, self.columns)

    def to_records(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]


@dataclass(frozen=True)
class DatabricksSqlConfig:
    server_hostname: str
    http_path: str
    access_token: str | None = field(default=None, repr=False)
    use_oauth: bool = False
    user_agent_entry: str = "rbac-rag-api"

    @classmethod
    def from_env(cls) -> "DatabricksSqlConfig":
        server_hostname = os.getenv("DATABRICKS_SERVER_HOSTNAME") or _host_to_server_hostname(
            os.getenv("DATABRICKS_HOST", "")
        )
        http_path = os.getenv("DATABRICKS_HTTP_PATH", "")
        access_token = os.getenv("DATABRICKS_TOKEN")
        client_id = os.getenv("DATABRICKS_CLIENT_ID")
        client_secret = os.getenv("DATABRICKS_CLIENT_SECRET")
        if not server_hostname:
            raise ValueError("DATABRICKS_SERVER_HOSTNAME or DATABRICKS_HOST is required")
        if not http_path:
            raise ValueError("DATABRICKS_HTTP_PATH is required")
        if not ((client_id and client_secret) or access_token):
            raise ValueError(
                "DATABRICKS_CLIENT_ID/DATABRICKS_CLIENT_SECRET or DATABRICKS_TOKEN is required"
            )
        return cls(
            server_hostname=server_hostname,
            http_path=http_path,
            access_token=None if client_id and client_secret else access_token,
            use_oauth=bool(client_id and client_secret),
        )


def _host_to_server_hostname(host: str) -> str:
    return host.replace("https://", "").replace("http://", "").strip("/")


def _oauth_credentials_provider(server_hostname: str):
    def credential_provider():
        from databricks.sdk.core import Config, oauth_service_principal

        host = os.getenv("DATABRICKS_HOST", "").strip() or f"https://{server_hostname}"
        config = Config(
            host=host,
            client_id=os.getenv("DATABRICKS_CLIENT_ID"),
            client_secret=os.getenv("DATABRICKS_CLIENT_SECRET"),
        )
        return oauth_service_principal(config)

    return credential_provider


class DatabricksSqlClient:
    def __init__(self, config: DatabricksSqlConfig | None = None):
        self.config = config or DatabricksSqlConfig.from_env()

    def sql(self, statement: str, args: dict[str, Any] | Iterable[Any] | None = None) -> SqlResult:
        from databricks import sql

        query, parameters = _prepare_statement(statement, args)
        connect_kwargs: dict[str, Any] = {
            "server_hostname": self.config.server_hostname,
            "http_path": self.config.http_path,
            "user_agent_entry": self.config.user_agent_entry,
        }
        if self.config.use_oauth:
            connect_kwargs["credentials_provider"] = _oauth_credentials_provider(
                self.config.server_hostname
            )
        else:
            connect_kwargs["access_token"] = self.config.access_token

        with sql.connect(**connect_kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters=parameters)
                if cursor.description is None:
                    return SqlResult([], [])
                columns = [column[0] for column in cursor.description]
                return SqlResult(_rows_to_dicts(cursor.fetchall(), columns), columns)


def _prepare_statement(
    statement: str,
    args: dict[str, Any] | Iterable[Any] | None,
) -> tuple[str, list[Any] | None]:
    if args is None:
        return statement, None
    if isinstance(args, dict):
        parameters: list[Any] = []

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in args:
                raise ValueError(f"Missing SQL parameter: {name}")
            parameters.append(args[name])
            return "?"

        return PARAMETER_PATTERN.sub(replace, statement), parameters
    return statement, list(args)


def _rows_to_dicts(rows: list[Any], columns: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            records.append(dict(row))
        elif hasattr(row, "asDict"):
            records.append(row.asDict())
        elif hasattr(row, "_asdict"):
            records.append(dict(row._asdict()))
        else:
            records.append(dict(zip(columns, row)))
    return records
