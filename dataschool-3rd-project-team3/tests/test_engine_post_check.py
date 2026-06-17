from types import SimpleNamespace

from rbac_rag.engine import RagEngine, is_post_check_failure
from rbac_rag.settings import RagSettings


class FakeSql:
    def sql(self, statement, args=None):
        if "access_policies" in statement:
            return FakeRoleDataFrame()
        return FakeDataFrame()


class FakeRoleDataFrame:
    def collect(self):
        return [SimpleNamespace(system_name="GROUPWARE")]


class FakeDataFrame:
    def limit(self, count):
        return self

    def toPandas(self):
        return FakeTable()


class FakeTable:
    columns = ["event_id"]

    def __len__(self):
        return 1

    def to_string(self, index=False):
        return "event_id\nE1"


class FakeMappings:
    table_id_to_fqn = {"synthetic__events": "cos_adb.silver.events"}
    table_columns = {"cos_adb.silver.events": ["event_id", "status"]}

    def get_all_table_list(self):
        return "  - cos_adb.silver.events"

    def get_all_tables(self):
        return {"cos_adb.silver.events"}

    def get_all_domains(self):
        return ["Event"]

    def get_allowed_table_list(self, domains):
        return "  - cos_adb.silver.events"

    def get_allowed_tables(self, domains):
        return {"cos_adb.silver.events"}

    def get_table_id_mapping_str(self, domains):
        return "  synthetic__events -> cos_adb.silver.events"


class FakeLlm:
    def __init__(self):
        self.post_check_calls = 0

    def search_metadata(self, *args, **kwargs):
        return [["table", "synthetic__events", "events context", "Event"]]

    def build_context(self, results):
        return "context"

    def generate_sql(self, *args, **kwargs):
        return "```sql\nSELECT event_id FROM cos_adb.silver.events LIMIT 20\n```"

    def extract_sql(self, text):
        return "SELECT event_id FROM cos_adb.silver.events LIMIT 20"

    def post_check(self, *args, **kwargs):
        self.post_check_calls += 1
        return "PASS"

    def summarize_results(self, *args, **kwargs):
        return "answer"


class RetryColumnLlm(FakeLlm):
    def __init__(self):
        super().__init__()
        self.generate_sql_calls = 0

    def generate_sql(self, *args, **kwargs):
        self.generate_sql_calls += 1
        if self.generate_sql_calls == 1:
            return "SELECT manual_id FROM cos_adb.silver.events LIMIT 20"
        return "SELECT event_id FROM cos_adb.silver.events LIMIT 20"

    def extract_sql(self, text):
        return text


class AlwaysBadColumnLlm(RetryColumnLlm):
    def generate_sql(self, *args, **kwargs):
        self.generate_sql_calls += 1
        return "SELECT manual_id FROM cos_adb.silver.events LIMIT 20"


class DenyPostCheckLlm(FakeLlm):
    def post_check(self, *args, **kwargs):
        self.post_check_calls += 1
        return "DENY: generated answer exposes restricted data"


def build_engine(fake_llm):
    return RagEngine(
        spark=FakeSql(),
        llm=fake_llm,
        settings=RagSettings(),
        mappings=FakeMappings(),
        selected_role="GENERAL_EMPLOYEE",
        rbac_enabled=False,
        post_check_enabled=True,
        allowed_domains=[],
        valid_role_ids=["GENERAL_EMPLOYEE"],
        audit_logger=lambda output: "log-1",
        display_results=False,
    )


def test_engine_skips_post_check_when_disabled() -> None:
    fake_llm = FakeLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show events",
        role_id="GENERAL_EMPLOYEE",
        rbac_enabled=True,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert result["post_check"] is False
    assert fake_llm.post_check_calls == 0


def test_engine_runs_post_check_when_enabled() -> None:
    fake_llm = FakeLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show events",
        role_id="GENERAL_EMPLOYEE",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert result["post_check"] is True
    assert fake_llm.post_check_calls == 1


def test_engine_blocks_post_check_deny_verdict() -> None:
    fake_llm = DenyPostCheckLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show events",
        role_id="GENERAL_EMPLOYEE",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "DENIED"
    assert result["failure_reason"] == "POST_CHECK_FAILED"
    assert fake_llm.post_check_calls == 1


def test_post_check_failure_parser_accepts_block_and_deny() -> None:
    assert is_post_check_failure("FAIL: restricted")
    assert is_post_check_failure("DENY: restricted")
    assert is_post_check_failure("BLOCKED: restricted")
    assert not is_post_check_failure("PASS: allowed")


def test_engine_retries_when_generated_sql_uses_unknown_column() -> None:
    fake_llm = RetryColumnLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show customer service manuals",
        role_id="GENERAL_EMPLOYEE",
        rbac_enabled=True,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert result["sql"] == "SELECT event_id FROM cos_adb.silver.events LIMIT 20"
    assert fake_llm.generate_sql_calls == 2


def test_engine_falls_back_when_retry_repeats_unknown_column() -> None:
    fake_llm = AlwaysBadColumnLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show customer service manuals",
        role_id="GENERAL_EMPLOYEE",
        rbac_enabled=True,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert result["sql"] == "SELECT event_id, status FROM cos_adb.silver.events LIMIT 20"
    assert fake_llm.generate_sql_calls == 2
