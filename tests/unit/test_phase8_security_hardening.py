"""Phase 8 security hardening: uploads, authentication and authorisation."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.auth import AccountLockedError, AuthenticationError, Role
from app.auth.provider import PermissionDeniedError
from app.ingestion.config import IngestionConfig
from app.ingestion.storage import LocalWorkbookStorage, WorkbookStorageError
from app.ingestion.workbook import (
    UnsupportedWorkbookError,
    validate_workbook_file,
)
from tests.fixtures.phase6_helpers import PASSWORD, create_user


def _xlsx_bytes(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        for name, payload in (extra or {}).items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# -- upload hardening -------------------------------------------------------


def test_an_oversized_upload_is_refused():
    config = IngestionConfig(max_upload_bytes=1024)
    with pytest.raises(UnsupportedWorkbookError):
        validate_workbook_file("big.xlsx", b"PK\x03\x04" + b"0" * 4096, config=config)


@pytest.mark.parametrize("name", ["data.xls", "data.csv", "data.xlsx.exe", "data"])
def test_an_unsupported_extension_is_refused(name):
    with pytest.raises(UnsupportedWorkbookError):
        validate_workbook_file(name, _xlsx_bytes())


def test_a_path_traversal_filename_is_reduced_to_its_basename():
    workbook = validate_workbook_file("../../../etc/evil.xlsx", _xlsx_bytes())
    assert workbook.filename == "evil.xlsx"
    assert "/" not in workbook.filename


def test_a_decompression_bomb_is_refused():
    payload = _xlsx_bytes({"xl/bomb.xml": b"0" * (8 * 1024 * 1024)})
    config = IngestionConfig(
        max_uncompressed_bytes=1024 * 1024, max_compression_ratio=1000.0
    )
    with pytest.raises(UnsupportedWorkbookError) as error:
        validate_workbook_file("bomb.xlsx", payload, config=config)
    assert "decompression" in str(error.value).casefold()


def test_an_implausible_compression_ratio_is_refused():
    payload = _xlsx_bytes({"xl/bomb.xml": b"0" * (8 * 1024 * 1024)})
    config = IngestionConfig(max_compression_ratio=2.0)
    with pytest.raises(UnsupportedWorkbookError) as error:
        validate_workbook_file("bomb.xlsx", payload, config=config)
    assert "compression ratio" in str(error.value).casefold()


def test_too_many_archive_entries_are_refused():
    payload = _xlsx_bytes({f"xl/part{index}.xml": b"<x/>" for index in range(30)})
    with pytest.raises(UnsupportedWorkbookError):
        validate_workbook_file(
            "many.xlsx", payload, config=IngestionConfig(max_archive_entries=5)
        )


def test_an_unsafe_internal_archive_path_is_refused():
    payload = _xlsx_bytes({"xl/../../evil.xml": b"<x/>"})
    with pytest.raises(UnsupportedWorkbookError) as error:
        validate_workbook_file("traversal.xlsx", payload)
    assert "unsafe internal path" in str(error.value).casefold()


def test_a_macro_enabled_workbook_is_flagged_and_never_executed():
    workbook = validate_workbook_file(
        "macro.xlsm", _xlsx_bytes({"xl/vbaProject.bin": b"\x00\x01"})
    )
    assert workbook.macro_enabled is True
    assert any("macro" in warning.casefold() for warning in workbook.warnings)


def test_a_legacy_or_encrypted_workbook_is_refused():
    from app.ingestion.workbook import ProtectedWorkbookError

    with pytest.raises(ProtectedWorkbookError):
        validate_workbook_file(
            "legacy.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 64
        )


def test_workbooks_are_read_without_formula_evaluation():
    from pathlib import Path

    source = Path("app/ingestion/workbook.py").read_text(encoding="utf-8")
    assert "data_only=True" in source
    assert "keep_links=False" in source


def test_storage_refuses_a_uri_outside_the_upload_root(tmp_path):
    storage = LocalWorkbookStorage(root=tmp_path)
    with pytest.raises(WorkbookStorageError):
        storage.retrieve("file:///etc/passwd")
    with pytest.raises(WorkbookStorageError):
        storage.retrieve(f"{storage.scheme}://../../etc/passwd")
    assert storage.exists(f"{storage.scheme}:///etc/passwd") is False


def test_storage_round_trips_inside_the_root(tmp_path):
    storage = LocalWorkbookStorage(root=tmp_path)
    uri = storage.store(
        content_hash="a" * 64, filename="../../evil.xlsx", payload=b"payload"
    )
    assert storage.retrieve(uri) == b"payload"
    assert "evil.xlsx" in uri and ".." not in uri


# -- authentication hardening ----------------------------------------------


def test_passwords_are_hashed_with_a_salted_slow_kdf():
    from app.auth.passwords import hash_password, verify_password

    stored = hash_password(PASSWORD)
    assert PASSWORD not in stored
    assert stored.startswith("pbkdf2_sha256$")
    assert int(stored.split("$")[1]) >= 200_000
    assert hash_password(PASSWORD) != stored  # unique salt
    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong", stored) is False


def test_repeated_failed_sign_ins_lock_the_account(session_factory):
    from app.auth import LocalPasswordAuthenticationProvider

    provider = LocalPasswordAuthenticationProvider(
        session_factory, max_failed_logins=3
    )
    create_user(provider, "lock.target", Role.SALES_USER)
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            provider.authenticate("lock.target", "wrong-password")
    with pytest.raises(AccountLockedError):
        provider.authenticate("lock.target", PASSWORD)


def test_a_successful_sign_in_clears_the_failure_counter(session_factory):
    from app.auth import LocalPasswordAuthenticationProvider

    provider = LocalPasswordAuthenticationProvider(
        session_factory, max_failed_logins=3
    )
    create_user(provider, "clear.target", Role.SALES_USER)
    with pytest.raises(AuthenticationError):
        provider.authenticate("clear.target", "wrong-password")
    assert provider.authenticate("clear.target", PASSWORD).username == "clear.target"
    with pytest.raises(AuthenticationError):
        provider.authenticate("clear.target", "wrong-password")
    assert provider.authenticate("clear.target", PASSWORD) is not None


def test_an_unknown_user_and_a_bad_password_look_identical(session_factory, people):
    from app.auth import LocalPasswordAuthenticationProvider

    provider = LocalPasswordAuthenticationProvider(session_factory)
    with pytest.raises(AuthenticationError) as unknown:
        provider.authenticate("ghost.user", PASSWORD)
    with pytest.raises(AuthenticationError) as bad:
        provider.authenticate("sam.sales", "wrong-password")
    assert str(unknown.value) == str(bad.value)


def test_a_session_expires_and_cannot_be_resolved(session_factory):
    from datetime import timedelta

    from app.auth import LocalPasswordAuthenticationProvider

    provider = LocalPasswordAuthenticationProvider(
        session_factory, session_lifetime=timedelta(seconds=-1)
    )
    user = create_user(provider, "expired.user", Role.SALES_USER)
    assert provider.resolve_session(user.session_token) is None


def test_a_revoked_session_cannot_be_resolved(session_factory, people):
    from app.auth import LocalPasswordAuthenticationProvider

    provider = LocalPasswordAuthenticationProvider(session_factory)
    user = provider.authenticate("sam.sales", PASSWORD)
    assert provider.resolve_session(user.session_token) is not None
    provider.end_session(user.session_token)
    assert provider.resolve_session(user.session_token) is None


def test_the_login_audit_record_never_contains_a_credential(
    session_factory, auth_provider
):
    from app.services.unit_of_work import UnitOfWork

    create_user(auth_provider, "audited.user", Role.SALES_USER)
    with UnitOfWork(session_factory) as uow:
        events = uow.audit_events.list_recent(limit=500)
    serialised = " ".join(str(event.details) for event in events)
    assert PASSWORD not in serialised
    assert "password" not in serialised.casefold()


# -- authorisation hardening -----------------------------------------------


def test_only_an_administrator_may_activate_a_pricing_data_version(
    session_factory, people
):
    from app.services.pricing_data_admin import PricingDataAdminService

    admin_service = PricingDataAdminService(session_factory)
    for role in ("sales", "manager", "pricing"):
        with pytest.raises(PermissionDeniedError):
            admin_service.activate(1, user=people[role])
        with pytest.raises(PermissionDeniedError):
            admin_service.publish(1, user=people[role])
    with pytest.raises(PermissionDeniedError):
        admin_service.activate(1, user=None)
    with pytest.raises(PermissionDeniedError):
        admin_service.deactivate_all(user=people["sales"])


def test_an_administrator_activation_is_audited(session_factory, people, tmp_path):
    from app.ingestion.repository import PricingDataRepository
    from app.services.pricing_data_admin import PricingDataAdminService
    from app.services.unit_of_work import UnitOfWork

    repository = PricingDataRepository(session_factory)
    version = repository.register_synthetic_fallback(
        label="phase8-admin-check", row_count=3
    )
    service = PricingDataAdminService(session_factory, repository=repository)
    activated = service.activate(version.id, user=people["admin"])
    assert activated.is_active is True

    with UnitOfWork(session_factory) as uow:
        events = uow.audit_events.list_recent(limit=500)
    types = [event.event_type for event in events]
    assert "pricing_data_version_activated" in types


def test_a_role_cannot_be_assigned_from_free_text(auth_provider):
    from app.auth.roles import UnknownRoleError

    with pytest.raises(UnknownRoleError):
        auth_provider.create_user(
            username="rogue.admin",
            password=PASSWORD,
            roles=("super_admin",),
        )


# -- secrets ----------------------------------------------------------------


def _looks_like_a_credential(value: str) -> bool:
    """A crude but useful shape test for an accidentally committed secret."""

    import re

    if len(value) < 20:
        return False
    if re.fullmatch(r"[A-Za-z0-9_.\-/:@]+", value) is None:
        return False
    if value.startswith(("sqlite", "postgres", "http", "./", "/", "<")):
        return False
    return bool(re.search(r"[A-Za-z]", value) and re.search(r"[0-9]", value))


def test_no_secret_file_is_committed():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert not (root / ".env").exists()
    example = (root / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        _, _, value = line.partition("=")
        cleaned = value.strip().strip('"')
        # Every documented value must be an obvious placeholder, never a
        # credential. Secret variables are documented with an empty value.
        assert not _looks_like_a_credential(cleaned), line


def test_operational_status_never_exposes_a_connection_string():
    from app.operations.status import redact_database_url

    redacted = redact_database_url(
        "postgresql+psycopg://appuser:" + "sup3rs3cret" + "@db.internal:5432/quotation"
    )
    assert "sup3rs3cret" not in redacted
    assert "appuser" not in redacted
    assert "postgresql" in redacted


def test_agent_configuration_reports_presence_not_values():
    from app.agents.config import load_agent_config

    config = load_agent_config(
        "agent1", {"AGENT1_PROVIDER": "openai_compatible", "AGENT1_API_KEY": "sk-should-not-leak"}
    )
    described = str(config.describe())
    assert "sk-should-not-leak" not in described


def test_email_configuration_reports_presence_not_values():
    from app.emailing.config import load_email_config

    config = load_email_config(
        {
            "EMAIL_PROVIDER": "smtp",
            "EMAIL_SMTP_HOST": "smtp.internal",
            "EMAIL_SMTP_PASSWORD": "smtp-should-not-leak",
            "EMAIL_SENDER_ADDRESS": "bot@internal.invalid",
        }
    )
    assert "smtp-should-not-leak" not in str(config.describe())
