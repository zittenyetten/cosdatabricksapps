from rbac_rag.runner import _post_check_status, _pre_check_status


def test_runner_marks_post_check_failure_blocked() -> None:
    result = {
        "post_check": True,
        "failure_reason": "POST_CHECK_FAILED",
    }

    assert _post_check_status(result) == "BLOCKED"


def test_runner_marks_post_check_skipped_before_execution() -> None:
    result = {
        "post_check": True,
        "failure_reason": "SQL_VALIDATION_ERROR",
    }

    assert _post_check_status(result) == "SKIPPED"


def test_runner_pre_check_only_blocks_pre_check_failures() -> None:
    assert _pre_check_status({"failure_reason": "RBAC_DOMAIN_DENIED"}) == "BLOCKED"
    assert _pre_check_status({"failure_reason": "POST_CHECK_FAILED"}) == "PASS"
