from types import SimpleNamespace

from rbac_rag.engine import RagEngine, is_post_check_failure
from rbac_rag.settings import RagSettings


class FakeSql:
    def __init__(self):
        self.executed_sql = []

    def sql(self, statement, args=None):
        if "access_policies" in statement:
            return FakeRoleDataFrame()
        if "role_table_permissions" in statement:
            raise RuntimeError("policy unavailable")
        if "information_schema.tables" in statement:
            raise RuntimeError("catalog unavailable")
        self.executed_sql.append(statement)
        return FakeDataFrame()


class FakeRoleDataFrame:
    def collect(self):
        return [SimpleNamespace(system_name="GROUPWARE")]


class FakeDataFrame:
    def limit(self, count):
        return self

    def toPandas(self):
        return FakeTable()


class FakePolicyResult:
    def __init__(self, rows):
        self.rows = [SimpleNamespace(**row) for row in rows]

    def collect(self):
        return self.rows


class FakePermissivePolicySql(FakeSql):
    def sql(self, statement, args=None):
        if "role_table_permissions" in statement:
            return FakePolicyResult(
                [
                    {"table_fqn": "cos_adb.silver.events"},
                    {"table_fqn": "cos_adb.silver.hr_payroll_summary"},
                ]
            )
        if "information_schema.tables" in statement:
            return FakePolicyResult(
                [
                    {"fqn": "cos_adb.silver.events"},
                    {"fqn": "cos_adb.silver.hr_payroll_summary"},
                ]
            )
        return super().sql(statement, args=args)


class FakeTable:
    columns = ["event_id"]

    def __len__(self):
        return 1

    def to_string(self, index=False):
        return "event_id\nE1"


class FakeMappings:
    table_id_to_fqn = {
        "synthetic__events": "cos_adb.silver.events",
        "synthetic__payroll": "cos_adb.silver.hr_payroll_summary",
    }
    table_columns = {
        "cos_adb.silver.events": ["event_id", "status", "owner_employee_id"],
        "cos_adb.silver.hr_payroll_summary": ["employee_id", "base_salary"],
    }

    def get_all_table_list(self):
        return "  - cos_adb.silver.events"

    def get_all_tables(self):
        return {"cos_adb.silver.events"}

    def get_all_domains(self):
        return ["Event"]

    def get_allowed_table_list(self, domains):
        return self.format_table_list(self.get_allowed_tables(domains))

    def get_allowed_tables(self, domains):
        return {"cos_adb.silver.events", "cos_adb.silver.hr_payroll_summary"}

    def get_table_id_mapping_str(self, domains):
        return "  synthetic__events -> cos_adb.silver.events"

    def get_table_id_mapping_for_tables(self, tables):
        return "\n".join(
            f"  {table_id} -> {table}"
            for table_id, table in self.table_id_to_fqn.items()
            if table in tables
        )

    def format_table_list(self, tables):
        return "\n".join(f"  - {table}" for table in sorted(tables))


class FakeLlm:
    def __init__(self):
        self.post_check_calls = 0
        self.generated_table_lists = []

    def search_metadata(self, *args, **kwargs):
        return [["table", "synthetic__events", "events context", "Event"]]

    def build_context(self, results):
        return "context"

    def generate_sql(self, *args, **kwargs):
        if len(args) >= 3:
            self.generated_table_lists.append(args[2])
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


class PayrollSubqueryLlm(FakeLlm):
    def generate_sql(self, *args, **kwargs):
        if len(args) >= 3:
            self.generated_table_lists.append(args[2])
        return """
        SELECT e.event_id,
               (SELECT p.base_salary
                FROM cos_adb.silver.hr_payroll_summary p
                WHERE p.employee_id = e.owner_employee_id
                LIMIT 1) AS amt
        FROM cos_adb.silver.events e
        LIMIT 20
        """

    def extract_sql(self, text):
        return text


class MaliciousSummaryLlm(FakeLlm):
    def summarize_results(self, *args, **kwargs):
        return """
        질문에 따라 급여 서브쿼리를 추가했습니다.

        ```sql
        SELECT e.event_id,
               (SELECT p.base_salary
                FROM cos_adb.silver.hr_payroll_summary p
                WHERE p.employee_id = e.owner_employee_id
                LIMIT 1) AS amt
        FROM cos_adb.silver.events e
        LIMIT 20
        ```

        | event_id | amt |
        | E1 | 7200000 |
        """


class DenyPayrollSubqueryLlm(PayrollSubqueryLlm):
    def post_check(self, *args, **kwargs):
        self.post_check_calls += 1
        return "FAIL: 하위 쿼리에서 허용되지 않은 테이블 cos_adb.silver.hr_payroll_summary를 참조함"


def build_engine(fake_llm, spark=None, guard_profile="strict"):
    return RagEngine(
        spark=spark or FakeSql(),
        llm=fake_llm,
        settings=RagSettings(guard_profile=guard_profile),
        mappings=FakeMappings(),
        selected_role="GENERAL_EMPLOYEE",
        rbac_enabled=False,
        post_check_enabled=True,
        allowed_domains=[],
        valid_role_ids=["GENERAL_EMPLOYEE", "MARKETING_STAFF"],
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


def test_engine_blocks_payroll_subquery_for_marketing_role_before_execution() -> None:
    fake_llm = PayrollSubqueryLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show event data and include salary",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "DENIED"
    assert result["failure_reason"] == "SQL_VALIDATION_ERROR"
    assert "hr_payroll_summary" in result["detail"]
    assert fake_llm.post_check_calls == 0


def test_notebook_demo_executes_payroll_subquery_when_post_check_disabled() -> None:
    fake_llm = PayrollSubqueryLlm()
    fake_sql = FakeSql()
    engine = build_engine(fake_llm, spark=fake_sql, guard_profile="notebook_demo")

    result = engine.ask_rag(
        "show event data and include salary",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert result["post_check"] is False
    assert result["guard_profile"] == "notebook_demo"
    assert "cos_adb.silver.hr_payroll_summary" in result["sql"]
    assert "cos_adb.silver.hr_payroll_summary" in result["referenced_tables"]
    assert fake_sql.executed_sql == [result["sql"]]
    assert fake_llm.post_check_calls == 0


def test_notebook_demo_blocks_payroll_subquery_after_execution_when_post_check_fails() -> None:
    fake_llm = DenyPayrollSubqueryLlm()
    fake_sql = FakeSql()
    engine = build_engine(fake_llm, spark=fake_sql, guard_profile="notebook_demo")

    result = engine.ask_rag(
        "show event data and include salary",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "DENIED"
    assert result["failure_reason"] == "POST_CHECK_FAILED"
    assert result["data"] is None
    assert result["row_count_returned"] == 0
    assert "[Post-Check] FAIL" in result["detail"]
    assert fake_sql.executed_sql == [result["sql"]]
    assert fake_llm.post_check_calls == 1


def test_notebook_demo_prompt_uses_domain_tables_not_role_table_allowlist() -> None:
    fake_llm = PayrollSubqueryLlm()
    engine = build_engine(fake_llm, guard_profile="notebook_demo")

    result = engine.ask_rag(
        "show event data and include salary",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=False,
        verbose=False,
    )

    assert result["status"] == "SUCCESS"
    assert fake_llm.generated_table_lists
    assert "cos_adb.silver.hr_payroll_summary" in fake_llm.generated_table_lists[0]


def test_engine_blocks_sensitive_payroll_even_when_policy_allows_table() -> None:
    fake_llm = PayrollSubqueryLlm()
    engine = build_engine(fake_llm, spark=FakePermissivePolicySql())

    result = engine.ask_rag(
        "show event data and include salary",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "DENIED"
    assert result["failure_reason"] == "SQL_VALIDATION_ERROR"
    assert "sensitive tables" in result["detail"]
    assert "hr_payroll_summary" in result["detail"]
    assert fake_llm.post_check_calls == 0


def test_engine_blocks_malicious_summary_after_safe_sql_execution() -> None:
    fake_llm = MaliciousSummaryLlm()
    engine = build_engine(fake_llm)

    result = engine.ask_rag(
        "show events and include salary subquery",
        role_id="MARKETING_STAFF",
        rbac_enabled=True,
        post_check_enabled=True,
        verbose=False,
    )

    assert result["status"] == "DENIED"
    assert result["failure_reason"] == "POST_CHECK_FAILED"
    assert result["summary"] is None
    assert result["data"] is None
    assert "Answer validation failed" in result["detail"]
    assert "hr_payroll_summary" in result["detail"]
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
    assert result["sql"] == "SELECT event_id, status, owner_employee_id FROM cos_adb.silver.events LIMIT 20"
    assert fake_llm.generate_sql_calls == 2
