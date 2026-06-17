import pytest

from rbac_rag.rbac import validate_role_id


def test_validate_role_id_accepts_known_role() -> None:
    assert validate_role_id("GENERAL_EMPLOYEE", ["GENERAL_EMPLOYEE"]) == "GENERAL_EMPLOYEE"


def test_validate_role_id_strips_spaces() -> None:
    assert validate_role_id(" GENERAL_EMPLOYEE ", ["GENERAL_EMPLOYEE"]) == "GENERAL_EMPLOYEE"


def test_validate_role_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        validate_role_id("", ["GENERAL_EMPLOYEE"])


def test_validate_role_id_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        validate_role_id("ADMIN", ["GENERAL_EMPLOYEE"])


def test_validate_role_id_rejects_invalid_characters() -> None:
    with pytest.raises(ValueError):
        validate_role_id("GENERAL_EMPLOYEE'; DROP TABLE x; --", ["GENERAL_EMPLOYEE"])

