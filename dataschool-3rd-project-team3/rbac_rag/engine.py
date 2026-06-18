import re
import time
import uuid
from typing import Any, Callable

from .answer_guard import validate_answer_summary
from .llm import DatabricksLLM
from .logging_utils import kst_now, save_rag_log
from .mappings import TableMappings
from .prompts import MSG_ACCESS_DENIED
from .rbac import (
    UNIVERSAL_DOMAINS,
    get_allowed_domains,
    get_role_table_access,
    get_sensitive_table_denials,
    validate_role_id,
)
from .settings import RagSettings
from .sql_validator import (
    SqlValidationError,
    build_safe_projection_sql,
    validate_basic_select_sql,
    validate_select_sql,
)


EventCallback = Callable[[str, dict[str, Any]], None]


class RagEngine:
    def __init__(
        self,
        *,
        spark: Any,
        llm: DatabricksLLM,
        settings: RagSettings,
        mappings: TableMappings,
        selected_role: str,
        rbac_enabled: bool,
        post_check_enabled: bool,
        allowed_domains: list[str] | None,
        valid_role_ids: list[str] | None = None,
        audit_logger: Callable[[dict[str, Any]], str | None] | None = None,
        display_results: bool = True,
    ):
        self.spark = spark
        self.llm = llm
        self.settings = settings
        self.mappings = mappings
        self.selected_role = selected_role
        self.rbac_enabled = rbac_enabled
        self.post_check_enabled = post_check_enabled
        self.allowed_domains = allowed_domains or []
        self.valid_role_ids = set(valid_role_ids or [])
        self.audit_logger = audit_logger
        self.display_results = display_results

        self.rbac_table_list = (
            self.mappings.get_allowed_table_list(self.allowed_domains)
            if self.allowed_domains
            else self.mappings.get_all_table_list()
        )

    def ask_rag(
        self,
        question: str,
        *,
        top_k: int | None = None,
        role_id: str | None = None,
        rbac_enabled: bool | None = None,
        post_check_enabled: bool | None = None,
        event_callback: EventCallback | None = None,
        verbose: bool = True,
    ) -> dict[str, Any]:
        top_k = top_k or self.settings.top_k_default
        use_rbac = self.rbac_enabled if rbac_enabled is None else rbac_enabled
        use_post_check = self.post_check_enabled if post_check_enabled is None else post_check_enabled
        active_role = (
            validate_role_id(role_id or self.selected_role, self.valid_role_ids or None)
            if use_rbac
            else None
        )

        request_id = str(uuid.uuid4())
        request_time = kst_now()

        output: dict[str, Any] = {
            "request_id": request_id,
            "query_time": request_time,
            "question": question,
            "role": active_role,
            "rbac_enabled": use_rbac,
            "post_check": use_post_check,
            "status": None,
            "execution_status": None,
            "permission_check": None,
            "success_reason": None,
            "failure_reason": None,
            "query_runtime_ms": None,
            "table_access": [],
            "referenced_tables": [],
            "sql": None,
            "columns_returned": [],
            "row_count_returned": None,
            "data": None,
            "summary": None,
            "detail": None,
            "guard_profile": self.settings.guard_profile,
        }

        if self.settings.guard_profile == "notebook_demo":
            return self._ask_rag_notebook_demo(
                question=question,
                top_k=top_k,
                role_id=role_id,
                active_role=active_role,
                use_rbac=use_rbac,
                use_post_check=use_post_check,
                output=output,
                event_callback=event_callback,
                verbose=verbose,
            )

        if use_rbac:
            domains = (
                get_allowed_domains(
                    self.spark,
                    active_role,
                    self.valid_role_ids or None,
                    self.settings.catalog,
                )
                if role_id
                else self.allowed_domains
            )
            domain_table_set = self.mappings.get_allowed_tables(domains)
            role_table_access = get_role_table_access(
                self.spark,
                active_role,
                self.valid_role_ids or None,
                self.settings.catalog,
            )
            allowed_table_set = role_table_access.tables
            if not allowed_table_set:
                output["status"] = "DENIED"
                output["detail"] = "허용된 업무 테이블이 없습니다. role-table 정책을 확인해 주세요."
                output["execution_status"] = "BLOCKED"
                output["permission_check"] = "DENY"
                output["failure_reason"] = "RBAC_TABLE_POLICY_EMPTY"
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output
            table_list = self.mappings.format_table_list(allowed_table_set)
            table_id_mapping = self.mappings.get_table_id_mapping_for_tables(allowed_table_set)
        else:
            domains = None
            table_list = self.mappings.get_all_table_list()
            table_id_mapping = self.mappings.get_table_id_mapping_str(self.mappings.get_all_domains())
            allowed_table_set = self.mappings.get_all_tables()

        _emit(
            event_callback,
            "rbac",
            enabled=use_rbac,
            role_id=active_role,
            allowed_domains=domains or [],
            role_table_source=role_table_access.source if use_rbac else None,
            role_table_warnings=role_table_access.warnings if use_rbac else [],
        )

        if use_rbac:
            _emit(event_callback, "retrieval", phase="pre_check", top_k=3)
            unfiltered = self.llm.search_metadata(
                question,
                top_k=3,
                vs_index_name=self.settings.vs_index_name,
            )
            needed = set(row[3] for row in unfiltered) - set(UNIVERSAL_DOMAINS)
            accessible = set(domains) - set(UNIVERSAL_DOMAINS)
            if needed and not needed.intersection(accessible):
                output["table_access"] = [
                    {
                        "table": self.mappings.table_id_to_fqn.get(row[1], row[1]),
                        "result": "DENIED",
                    }
                    for row in unfiltered
                    if row[3] not in set(UNIVERSAL_DOMAINS)
                ]
                output["status"] = "DENIED"
                output["detail"] = MSG_ACCESS_DENIED.format(role=active_role)
                output["execution_status"] = "BLOCKED"
                output["permission_check"] = "DENY"
                output["failure_reason"] = "RBAC_DOMAIN_DENIED"
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output

        _emit(event_callback, "retrieval", phase="context", top_k=top_k)
        results = self.llm.search_metadata(
            question,
            top_k=top_k,
            vs_index_name=self.settings.vs_index_name,
            allowed_domains=domains,
        )
        if not results:
            output["status"] = "DENIED"
            output["detail"] = "검색 결과 없음"
            output["execution_status"] = "BLOCKED"
            output["permission_check"] = "DENY"
            output["failure_reason"] = "NO_SEARCH_RESULT"
            self._save_log(output, event_callback)
            if verbose:
                print(format_output(output))
            return output

        context = self.llm.build_context(results)
        searched = sorted(set(row[1] for row in results))
        table_columns = getattr(self.mappings, "table_columns", {})
        referenced_tables: list[str] = []

        def validate_candidate_sql(candidate: str):
            nonlocal referenced_tables
            validation = validate_select_sql(
                candidate,
                allowed_table_set,
                table_columns=table_columns,
            )
            sensitive_denials = (
                get_sensitive_table_denials(active_role, validation.tables, self.settings.catalog)
                if use_rbac
                else []
            )
            if sensitive_denials:
                raise SqlValidationError(
                    "SQL references sensitive tables not allowed for "
                    f"role {active_role}: {', '.join(sensitive_denials)}"
                )
            referenced_tables = validation.tables
            output["referenced_tables"] = validation.tables
            return validation

        def generate_validated_sql(error_msg: str | None = None) -> str:
            _emit(event_callback, "sql_generation", retry=bool(error_msg))
            candidate = self.llm.extract_sql(
                self.llm.generate_sql(
                    question,
                    context,
                    table_list,
                    table_id_mapping=table_id_mapping,
                    error_msg=error_msg,
                )
            )
            output["sql"] = candidate
            validation = validate_candidate_sql(candidate)
            output["sql"] = validation.sql
            _emit(event_callback, "sql_validation", status="PASS", tables=validation.tables)
            return validation.sql

        try:
            sql = generate_validated_sql()
        except SqlValidationError as error:
            try:
                sql = generate_validated_sql(error_msg=str(error))
            except SqlValidationError as retry_error:
                fallback_sql = build_safe_projection_sql(
                    output.get("sql") or "",
                    allowed_table_set,
                    table_columns,
                )
                if fallback_sql:
                    validation = validate_candidate_sql(fallback_sql)
                    sql = validation.sql
                    output["sql"] = sql
                    _emit(
                        event_callback,
                        "sql_validation",
                        status="PASS",
                        tables=validation.tables,
                        fallback=True,
                    )
                else:
                    self._set_sql_validation_error(output, searched, str(retry_error), use_rbac)
                    _emit(event_callback, "sql_validation", status="BLOCKED", detail=output["detail"])
                    self._save_log(output, event_callback)
                    if verbose:
                        print(format_output(output))
                    return output

        query_started = time.perf_counter()
        for attempt in range(2):
            try:
                _emit(event_callback, "sql_execution", attempt=attempt + 1)
                df = self.spark.sql(sql)
                pdf = df.limit(20).toPandas()
                output["data"] = df
                output["columns_returned"] = list(pdf.columns)
                output["row_count_returned"] = len(pdf)
                output["query_runtime_ms"] = int((time.perf_counter() - query_started) * 1000)
                output["execution_status"] = "SUCCESS"
                break
            except Exception as error:
                if attempt == 0:
                    try:
                        sql = generate_validated_sql(error_msg=str(error))
                    except SqlValidationError as validation_error:
                        self._set_sql_validation_error(
                            output,
                            searched,
                            str(validation_error),
                            use_rbac,
                        )
                        output["query_runtime_ms"] = int(
                            (time.perf_counter() - query_started) * 1000
                        )
                        _emit(
                            event_callback,
                            "sql_validation",
                            status="BLOCKED",
                            detail=output["detail"],
                        )
                        self._save_log(output, event_callback)
                        if verbose:
                            print(format_output(output))
                        return output
                else:
                    access_tables = referenced_tables or [
                        self.mappings.table_id_to_fqn.get(table, table) for table in searched
                    ]
                    output["table_access"] = [
                        {"table": table, "result": "ERROR"} for table in access_tables
                    ]
                    output["status"] = "ERROR"
                    output["detail"] = str(error)[:300]
                    output["execution_status"] = "FAILED"
                    output["permission_check"] = "ALLOW" if use_rbac else None
                    output["failure_reason"] = "SQL_EXECUTION_ERROR"
                    output["query_runtime_ms"] = int((time.perf_counter() - query_started) * 1000)
                    output["row_count_returned"] = 0
                    self._save_log(output, event_callback)
                    if verbose:
                        print(format_output(output))
                    return output

        results_str = pdf.to_string(index=False)
        if use_post_check and use_rbac:
            _emit(event_callback, "post_check", status="RUNNING")
            verdict = self.llm.post_check(active_role, table_list, sql, results_str)
            if is_post_check_failure(verdict):
                access_tables = referenced_tables or [
                    self.mappings.table_id_to_fqn.get(table, table) for table in searched
                ]
                output["table_access"] = [
                    {"table": table, "result": "DENIED"} for table in access_tables
                ]
                output["status"] = "DENIED"
                output["detail"] = f"[Post-Check] {verdict}"
                output["data"] = None
                output["execution_status"] = "SUCCESS"
                output["permission_check"] = "DENY"
                output["success_reason"] = "SQL_EXECUTED"
                output["failure_reason"] = "POST_CHECK_FAILED"
                _emit(event_callback, "post_check", status="BLOCKED", verdict=verdict)
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output
            _emit(event_callback, "post_check", status="PASS", verdict=verdict)

        try:
            _emit(event_callback, "summarization", status="RUNNING")
            output["summary"] = self.llm.summarize_results(question, sql, results_str)
        except Exception as error:
            output["status"] = "ERROR"
            output["execution_status"] = "SUCCESS"
            output["permission_check"] = "ALLOW" if use_rbac else None
            output["success_reason"] = "SQL_EXECUTED"
            output["failure_reason"] = "SUMMARY_GENERATION_ERROR"
            output["detail"] = str(error)[:300]
            self._save_log(output, event_callback)
            if verbose:
                print(format_output(output))
            return output

        if use_post_check and use_rbac:
            answer_guard = validate_answer_summary(
                output["summary"] or "",
                role_id=active_role,
                catalog=self.settings.catalog,
                executed_sql=sql,
                referenced_tables=referenced_tables,
                returned_columns=output["columns_returned"],
                results_text=results_str,
            )
            if not answer_guard.allowed:
                access_tables = referenced_tables or [
                    self.mappings.table_id_to_fqn.get(table, table) for table in searched
                ]
                detail = "[Post-Check] Answer validation failed: " + "; ".join(
                    answer_guard.reasons
                )
                output["table_access"] = [
                    {"table": table, "result": "DENIED"} for table in access_tables
                ]
                output["status"] = "DENIED"
                output["detail"] = detail
                output["data"] = None
                output["summary"] = None
                output["execution_status"] = "SUCCESS"
                output["permission_check"] = "DENY"
                output["success_reason"] = "SQL_EXECUTED"
                output["failure_reason"] = "POST_CHECK_FAILED"
                output["answer_guard_reasons"] = answer_guard.reasons
                output["row_count_returned"] = 0
                _emit(event_callback, "summarization", status="BLOCKED", detail=detail)
                _emit(event_callback, "post_check", status="BLOCKED", verdict=detail)
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output
        _emit(event_callback, "summarization", status="SUCCESS")

        access_tables = referenced_tables or [
            self.mappings.table_id_to_fqn.get(table, table) for table in searched
        ]
        output["table_access"] = [{"table": table, "result": "SUCCESS"} for table in access_tables]
        output["status"] = "SUCCESS"
        output["execution_status"] = "SUCCESS"
        output["permission_check"] = "ALLOW" if use_rbac else None
        output["success_reason"] = "SQL_EXECUTED_AND_RESPONSE_RETURNED"
        output["failure_reason"] = None

        self._save_log(output, event_callback)

        if verbose and self.display_results:
            print(format_output(output))
            try:
                display(df.limit(20))
            except NameError:
                pass

        return output

    def _ask_rag_notebook_demo(
        self,
        *,
        question: str,
        top_k: int,
        role_id: str | None,
        active_role: str | None,
        use_rbac: bool,
        use_post_check: bool,
        output: dict[str, Any],
        event_callback: EventCallback | None,
        verbose: bool,
    ) -> dict[str, Any]:
        if use_rbac:
            domains = (
                get_allowed_domains(
                    self.spark,
                    active_role,
                    self.valid_role_ids or None,
                    self.settings.catalog,
                )
                if role_id
                else self.allowed_domains
            )
            table_list = self.mappings.get_allowed_table_list(domains)
            table_id_mapping = self.mappings.get_table_id_mapping_str(domains)
        else:
            domains = None
            table_list = self.mappings.get_all_table_list()
            table_id_mapping = self.mappings.get_table_id_mapping_str(self.mappings.get_all_domains())

        _emit(
            event_callback,
            "rbac",
            enabled=use_rbac,
            role_id=active_role,
            allowed_domains=domains or [],
            role_table_source="notebook_demo_domain_tables" if use_rbac else None,
            role_table_warnings=[],
            guard_profile="notebook_demo",
        )

        if use_rbac:
            _emit(event_callback, "retrieval", phase="pre_check", top_k=3)
            unfiltered = self.llm.search_metadata(
                question,
                top_k=3,
                vs_index_name=self.settings.vs_index_name,
            )
            needed = set(row[3] for row in unfiltered) - set(UNIVERSAL_DOMAINS)
            accessible = set(domains or []) - set(UNIVERSAL_DOMAINS)
            if needed and not needed.intersection(accessible):
                output["table_access"] = [
                    {
                        "table": self.mappings.table_id_to_fqn.get(row[1], row[1]),
                        "result": "DENIED",
                    }
                    for row in unfiltered
                    if row[3] not in set(UNIVERSAL_DOMAINS)
                ]
                output["status"] = "DENIED"
                output["detail"] = MSG_ACCESS_DENIED.format(role=active_role)
                output["execution_status"] = "BLOCKED"
                output["permission_check"] = "DENY"
                output["failure_reason"] = "RBAC_DOMAIN_DENIED"
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output

        _emit(event_callback, "retrieval", phase="context", top_k=top_k)
        results = self.llm.search_metadata(
            question,
            top_k=top_k,
            vs_index_name=self.settings.vs_index_name,
            allowed_domains=domains,
        )
        if not results:
            output["status"] = "DENIED"
            output["detail"] = "검색 결과 없음"
            output["execution_status"] = "BLOCKED"
            output["permission_check"] = "DENY"
            output["failure_reason"] = "NO_SEARCH_RESULT"
            self._save_log(output, event_callback)
            if verbose:
                print(format_output(output))
            return output

        context = self.llm.build_context(results)
        searched = sorted(set(row[1] for row in results))
        referenced_tables: list[str] = []

        def searched_tables() -> list[str]:
            return [self.mappings.table_id_to_fqn.get(table, table) for table in searched]

        def generate_basic_sql(error_msg: str | None = None) -> str:
            nonlocal referenced_tables
            _emit(event_callback, "sql_generation", retry=bool(error_msg))
            candidate = self.llm.extract_sql(
                self.llm.generate_sql(
                    question,
                    context,
                    table_list,
                    table_id_mapping=table_id_mapping,
                    error_msg=error_msg,
                )
            )
            output["sql"] = candidate
            validation = validate_basic_select_sql(candidate)
            referenced_tables = validation.tables
            output["referenced_tables"] = validation.tables
            output["sql"] = validation.sql
            _emit(event_callback, "sql_validation", status="PASS", tables=validation.tables)
            return validation.sql

        try:
            sql = generate_basic_sql()
        except SqlValidationError as error:
            try:
                sql = generate_basic_sql(error_msg=str(error))
            except SqlValidationError as retry_error:
                self._set_sql_validation_error(output, searched, str(retry_error), use_rbac)
                _emit(event_callback, "sql_validation", status="BLOCKED", detail=output["detail"])
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output

        query_started = time.perf_counter()
        for attempt in range(2):
            try:
                _emit(event_callback, "sql_execution", attempt=attempt + 1)
                df = self.spark.sql(sql)
                pdf = df.limit(20).toPandas()
                output["data"] = df
                output["columns_returned"] = list(pdf.columns)
                output["row_count_returned"] = len(pdf)
                output["query_runtime_ms"] = int((time.perf_counter() - query_started) * 1000)
                output["execution_status"] = "SUCCESS"
                break
            except Exception as error:
                if attempt == 0:
                    try:
                        sql = generate_basic_sql(error_msg=str(error))
                    except SqlValidationError as validation_error:
                        self._set_sql_validation_error(
                            output,
                            searched,
                            str(validation_error),
                            use_rbac,
                        )
                        output["query_runtime_ms"] = int(
                            (time.perf_counter() - query_started) * 1000
                        )
                        _emit(
                            event_callback,
                            "sql_validation",
                            status="BLOCKED",
                            detail=output["detail"],
                        )
                        self._save_log(output, event_callback)
                        if verbose:
                            print(format_output(output))
                        return output
                else:
                    output["table_access"] = [
                        {"table": table, "result": "ERROR"} for table in searched_tables()
                    ]
                    output["status"] = "ERROR"
                    output["detail"] = str(error)[:300]
                    output["execution_status"] = "FAILED"
                    output["permission_check"] = "ALLOW" if use_rbac else None
                    output["failure_reason"] = "SQL_EXECUTION_ERROR"
                    output["query_runtime_ms"] = int((time.perf_counter() - query_started) * 1000)
                    output["row_count_returned"] = 0
                    self._save_log(output, event_callback)
                    if verbose:
                        print(format_output(output))
                    return output

        results_str = pdf.to_string(index=False)
        if use_post_check and use_rbac:
            _emit(event_callback, "post_check", status="RUNNING")
            verdict = self.llm.post_check(active_role, table_list, sql, results_str)
            if is_post_check_failure(verdict):
                output["table_access"] = [
                    {"table": table, "result": "DENIED"} for table in searched_tables()
                ]
                output["status"] = "DENIED"
                output["detail"] = f"[Post-Check] {verdict}"
                output["data"] = None
                output["execution_status"] = "SUCCESS"
                output["permission_check"] = "DENY"
                output["success_reason"] = "SQL_EXECUTED"
                output["failure_reason"] = "POST_CHECK_FAILED"
                output["row_count_returned"] = 0
                _emit(event_callback, "post_check", status="BLOCKED", verdict=verdict)
                self._save_log(output, event_callback)
                if verbose:
                    print(format_output(output))
                return output
            _emit(event_callback, "post_check", status="PASS", verdict=verdict)
        else:
            _emit(event_callback, "post_check", status="SKIPPED")

        try:
            _emit(event_callback, "summarization", status="RUNNING")
            output["summary"] = self.llm.summarize_results(question, sql, results_str)
        except Exception as error:
            output["status"] = "ERROR"
            output["execution_status"] = "SUCCESS"
            output["permission_check"] = "ALLOW" if use_rbac else None
            output["success_reason"] = "SQL_EXECUTED"
            output["failure_reason"] = "SUMMARY_GENERATION_ERROR"
            output["detail"] = str(error)[:300]
            self._save_log(output, event_callback)
            if verbose:
                print(format_output(output))
            return output

        _emit(event_callback, "summarization", status="SUCCESS")

        output["table_access"] = [
            {"table": table, "result": "SUCCESS"} for table in searched_tables()
        ]
        output["status"] = "SUCCESS"
        output["execution_status"] = "SUCCESS"
        output["permission_check"] = "ALLOW" if use_rbac else None
        output["success_reason"] = "SQL_EXECUTED_AND_RESPONSE_RETURNED"
        output["failure_reason"] = None

        self._save_log(output, event_callback)

        if verbose and self.display_results:
            print(format_output(output))
            try:
                display(df.limit(20))
            except NameError:
                pass

        return output

    def _set_sql_validation_error(
        self,
        output: dict[str, Any],
        searched: list[str],
        detail: str,
        use_rbac: bool,
    ) -> None:
        output["table_access"] = [
            {
                "table": self.mappings.table_id_to_fqn.get(table, table),
                "result": "DENIED",
            }
            for table in searched
        ]
        column_error = detail.startswith("SQL references unavailable columns:")
        output["status"] = "ERROR" if column_error else "DENIED"
        output["detail"] = detail
        output["execution_status"] = "FAILED" if column_error else "BLOCKED"
        output["permission_check"] = "ALLOW" if column_error and use_rbac else "DENY" if use_rbac else None
        output["failure_reason"] = (
            "SQL_COLUMN_VALIDATION_ERROR" if column_error else "SQL_VALIDATION_ERROR"
        )
        output["row_count_returned"] = 0

    def _save_log(
        self,
        output: dict[str, Any],
        event_callback: EventCallback | None = None,
    ) -> str | None:
        log_id = (
            self.audit_logger(output)
            if self.audit_logger is not None
            else save_rag_log(self.spark, self.settings.log_table, output)
        )
        _emit(
            event_callback,
            "audit",
            log_id=log_id,
            execution_status=output.get("execution_status"),
        )
        return log_id


def _emit(callback: EventCallback | None, event: str, **payload: Any) -> None:
    if callback is not None:
        callback(event, payload)


def is_post_check_failure(verdict: str) -> bool:
    normalized = str(verdict or "").strip().upper()
    first_token = re.match(
        r"^[^A-Z]*(FAIL|DENY|DENIED|BLOCK|BLOCKED|REJECT|REJECTED|UNSAFE)\b",
        normalized,
    )
    return bool(first_token)


def format_output(output: dict[str, Any]) -> str:
    lines = [
        f"[{output['status']}] role={output['role']} "
        f"rbac={'ON' if output['rbac_enabled'] else 'OFF'} "
        f"post_check={'ON' if output['post_check'] else 'OFF'}"
    ]
    lines += [f"  {entry['table']} -> {entry['result']}" for entry in output["table_access"]]
    if output["detail"]:
        lines.append(f"  message: {output['detail']}")
    if output["sql"]:
        lines.append(f"  sql: {output['sql']}")
    if output["summary"]:
        lines.append(f"  summary: {output['summary']}")
    return "\n".join(lines)


def get_result(output: dict[str, Any], mode: str = "admin") -> dict[str, Any]:
    if mode == "admin":
        return {key: value for key, value in output.items() if key != "data"}
    if output["status"] == "SUCCESS":
        return {
            "answer": output["summary"],
            "data": output["data"].limit(20).toPandas().to_dict(orient="records") if output["data"] else [],
        }
    return {"answer": output["detail"] or "요청을 처리할 수 없습니다."}
