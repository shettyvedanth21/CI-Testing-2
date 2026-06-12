import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest


AUTH_SERVICE_ROOT = Path(__file__).resolve().parents[1]
SERVICES_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
for path in (REPO_ROOT, SERVICES_ROOT, AUTH_SERVICE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.chdir(AUTH_SERVICE_ROOT)
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "memory://")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters-long")

from app.models.auth import AuthActionType, User, UserRole
from app.services import auth_service as auth_service_module
from app.services.action_token_service import ActionTokenService
from app.services.auth_service import AuthService


UTC = timezone.utc


class FakeDB:
    async def execute(self, *args, **kwargs):
        return None

    async def flush(self):
        return None


def _make_user(
    *,
    user_id: str,
    tenant_id: str | None,
    role: UserRole = UserRole.VIEWER,
    is_active: bool = True,
    activated_at: datetime | None | object = ...,
):
    now = datetime.now(UTC).replace(tzinfo=None)
    return User(
        id=user_id,
        tenant_id=tenant_id,
        email=f"{user_id}@example.com",
        hashed_password="hashed-password",
        full_name="Test User",
        role=role,
        permissions_version=0,
        is_active=is_active,
        activated_at=now if activated_at is ... else activated_at,
        invited_at=None,
        deactivated_at=None,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


@pytest.mark.asyncio
async def test_send_invitation_invalidates_existing_tokens_and_sends_fresh_email_link(monkeypatch):
    user = _make_user(user_id="invitee", tenant_id="org-a", is_active=False, activated_at=None)
    invalidate_open_tokens = AsyncMock()
    create_token = AsyncMock(return_value="invite-token")
    send_invite_email = AsyncMock()

    monkeypatch.setattr(auth_service_module.action_token_svc, "invalidate_open_tokens", invalidate_open_tokens)
    monkeypatch.setattr(auth_service_module.action_token_svc, "create_token", create_token)
    monkeypatch.setattr(auth_service_module.mailer_svc, "send_invite_email", send_invite_email)
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=True)))

    await AuthService().send_invitation(
        FakeDB(),
        user=user,
        created_by_user_id="admin-1",
        created_by_role="org_admin",
        tenant_id="org-a",
    )

    assert user.invited_at is not None
    invalidate_open_tokens.assert_awaited_once_with(
        ANY,
        user_id=user.id,
        action_type=AuthActionType.INVITE_SET_PASSWORD,
    )
    create_token.assert_awaited_once_with(
        ANY,
        user_id=user.id,
        action_type=AuthActionType.INVITE_SET_PASSWORD,
        expires_in_minutes=ANY,
        created_by_user_id="admin-1",
        created_by_role="org_admin",
        tenant_id="org-a",
        metadata={"email": user.email},
    )
    send_invite_email.assert_awaited_once()
    payload = send_invite_email.await_args.kwargs
    assert payload["recipient"] == user.email
    assert payload["full_name"] == user.full_name
    assert payload["invite_link"].endswith("/accept-invite?token=invite-token")


@pytest.mark.asyncio
async def test_send_invitation_reopens_never_activated_user(monkeypatch):
    user = _make_user(user_id="invitee", tenant_id="org-a", is_active=False, activated_at=None)
    user.deactivated_at = datetime.now(UTC).replace(tzinfo=None)

    monkeypatch.setattr(auth_service_module.action_token_svc, "invalidate_open_tokens", AsyncMock())
    monkeypatch.setattr(auth_service_module.action_token_svc, "create_token", AsyncMock(return_value="invite-token"))
    monkeypatch.setattr(auth_service_module.mailer_svc, "send_invite_email", AsyncMock())
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=True)))

    await AuthService().send_invitation(
        FakeDB(),
        user=user,
        created_by_user_id="admin-1",
        created_by_role="org_admin",
        tenant_id="org-a",
    )

    assert user.deactivated_at is None


@pytest.mark.asyncio
async def test_invite_token_rejects_expired_links(monkeypatch):
    token_row = SimpleNamespace(
        action_type=AuthActionType.INVITE_SET_PASSWORD,
        used_at=None,
        expires_at=(datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None),
    )
    monkeypatch.setattr(ActionTokenService, "get_token_status", AsyncMock(return_value=token_row))

    with pytest.raises(Exception) as exc:
        await ActionTokenService().consume_token(
            FakeDB(),
            raw_token="expired-token",
            expected_action_type=AuthActionType.INVITE_SET_PASSWORD,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "ACTION_TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_invite_token_rejects_reuse_after_first_consumption(monkeypatch):
    token_row = SimpleNamespace(
        action_type=AuthActionType.INVITE_SET_PASSWORD,
        used_at=datetime.now(UTC).replace(tzinfo=None),
        expires_at=(datetime.now(UTC) + timedelta(minutes=15)).replace(tzinfo=None),
    )
    monkeypatch.setattr(ActionTokenService, "get_token_status", AsyncMock(return_value=token_row))

    with pytest.raises(Exception) as exc:
        await ActionTokenService().consume_token(
            FakeDB(),
            raw_token="used-token",
            expected_action_type=AuthActionType.INVITE_SET_PASSWORD,
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "ACTION_TOKEN_USED"


@pytest.mark.asyncio
async def test_request_password_reset_invalidates_open_tokens_and_sends_email(monkeypatch):
    user = _make_user(user_id="reset-user", tenant_id="org-a")
    invalidate_open_tokens = AsyncMock()
    create_token = AsyncMock(return_value="reset-token")
    send_reset_email = AsyncMock()

    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=True)))
    monkeypatch.setattr(auth_service_module.action_token_svc, "invalidate_open_tokens", invalidate_open_tokens)
    monkeypatch.setattr(auth_service_module.action_token_svc, "create_token", create_token)
    monkeypatch.setattr(auth_service_module.mailer_svc, "send_password_reset_email", send_reset_email)

    await AuthService().request_password_reset(FakeDB(), email=user.email)

    invalidate_open_tokens.assert_awaited_once_with(
        ANY,
        user_id=user.id,
        action_type=AuthActionType.PASSWORD_RESET,
    )
    create_token.assert_awaited_once_with(
        ANY,
        user_id=user.id,
        action_type=AuthActionType.PASSWORD_RESET,
        expires_in_minutes=ANY,
        created_by_user_id=user.id,
        created_by_role=user.role.value,
        tenant_id=user.tenant_id,
        metadata={"email": user.email},
    )
    send_reset_email.assert_awaited_once()
    payload = send_reset_email.await_args.kwargs
    assert payload["recipient"] == user.email
    assert payload["reset_link"].endswith("/reset-password?token=reset-token")


@pytest.mark.asyncio
async def test_request_password_reset_suppresses_suspended_org(monkeypatch):
    user = _make_user(user_id="reset-user", tenant_id="org-a")
    send_reset_email = AsyncMock()

    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=False)))
    monkeypatch.setattr(auth_service_module.mailer_svc, "send_password_reset_email", send_reset_email)

    await AuthService().request_password_reset(FakeDB(), email=user.email)

    send_reset_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_rejects_user_with_pending_password_setup(monkeypatch):
    user = _make_user(user_id="invite-user", tenant_id="org-a")

    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(
        FakeDB,
        "execute",
        AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "token-row")),
    )

    with pytest.raises(Exception) as exc:
        await AuthService().login(FakeDB(), user.email, "Password123!")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "PASSWORD_SETUP_REQUIRED"


@pytest.mark.asyncio
async def test_login_query_filters_out_expired_invite_tokens(monkeypatch):
    user = _make_user(user_id="invite-user", tenant_id="org-a")
    captured = {}

    async def fake_execute(self, statement, *args, **kwargs):
        captured["statement"] = statement
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(FakeDB, "execute", fake_execute)
    monkeypatch.setattr(auth_service_module.pwd_ctx, "verify", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=True, entitlements_version=0)))
    monkeypatch.setattr(auth_service_module.user_repo, "get_plant_ids", AsyncMock(return_value=["plant-a"]))
    monkeypatch.setattr(auth_service_module.token_svc, "generate_refresh_token_pair", lambda: ("raw-refresh", "hash"))
    monkeypatch.setattr(auth_service_module.token_svc, "create_access_token_async", AsyncMock(return_value="access-token"))
    monkeypatch.setattr(auth_service_module.token_svc, "store_refresh_token", AsyncMock())

    _, token_response = await AuthService().login(FakeDB(), user.email, "Password123!")

    assert token_response.access_token == "access-token"
    where_clause = str(getattr(captured["statement"], "whereclause", captured["statement"]))
    assert "expires_at" in where_clause


@pytest.mark.asyncio
async def test_login_rejects_disabled_account_before_password_verification(monkeypatch):
    user = _make_user(user_id="disabled-user", tenant_id="org-a", is_active=False)

    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=user))
    monkeypatch.setattr(
        FakeDB,
        "execute",
        AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    verify_mock = AsyncMock()
    monkeypatch.setattr(auth_service_module.pwd_ctx, "verify", verify_mock)

    with pytest.raises(Exception) as exc:
        await AuthService().login(FakeDB(), user.email, "Password123!")

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ACCOUNT_DISABLED"
    verify_mock.assert_not_called()


@pytest.mark.asyncio
async def test_login_rejects_unknown_email_with_generic_invalid_credentials(monkeypatch):
    monkeypatch.setattr(auth_service_module.user_repo, "get_by_email", AsyncMock(return_value=None))

    with pytest.raises(Exception) as exc:
        await AuthService().login(FakeDB(), "missing@example.com", "Password123!")

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_reset_password_updates_hash_and_revokes_all_sessions(monkeypatch):
    user = _make_user(user_id="reset-user", tenant_id="org-a")
    token_row = SimpleNamespace(user_id=user.id)
    revoke_all_user_tokens = AsyncMock()

    monkeypatch.setattr(auth_service_module.action_token_svc, "consume_token", AsyncMock(return_value=token_row))
    monkeypatch.setattr(auth_service_module.user_repo, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(auth_service_module.org_repo, "get_by_id", AsyncMock(return_value=SimpleNamespace(id="org-a", is_active=True)))
    monkeypatch.setattr(auth_service_module.pwd_ctx, "hash", lambda password: f"hashed::{password}")
    monkeypatch.setattr(auth_service_module.token_svc, "revoke_all_user_tokens", revoke_all_user_tokens)

    await AuthService().reset_password(
        FakeDB(),
        token="reset-token",
        password="Password123!",
        confirm_password="Password123!",
    )

    assert user.hashed_password == "hashed::Password123!"
    revoke_all_user_tokens.assert_awaited_once_with(ANY, user.id)


@pytest.mark.asyncio
async def test_reset_password_rejects_mismatched_confirmation(monkeypatch):
    consume_token = AsyncMock()
    monkeypatch.setattr(auth_service_module.action_token_svc, "consume_token", consume_token)

    with pytest.raises(Exception) as exc:
        await AuthService().reset_password(
            FakeDB(),
            token="reset-token",
            password="Password123!",
            confirm_password="Password321!",
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "PASSWORD_MISMATCH"
    consume_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_action_token_when_user_missing(monkeypatch):
    token_row = SimpleNamespace(user_id="missing-user")

    monkeypatch.setattr(auth_service_module.action_token_svc, "consume_token", AsyncMock(return_value=token_row))
    monkeypatch.setattr(auth_service_module.user_repo, "get_by_id", AsyncMock(return_value=None))

    with pytest.raises(Exception) as exc:
        await AuthService().reset_password(
            FakeDB(),
            token="reset-token",
            password="Password123!",
            confirm_password="Password123!",
        )

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_ACTION_TOKEN"


@pytest.mark.asyncio
async def test_action_token_status_returns_valid_email_context(monkeypatch):
    user = _make_user(user_id="invitee", tenant_id="org-a", is_active=False, activated_at=None)
    token_row = SimpleNamespace(
        user_id=user.id,
        used_at=None,
        expires_at=(datetime.now(UTC) + timedelta(minutes=15)).replace(tzinfo=None),
        action_type=AuthActionType.INVITE_SET_PASSWORD,
    )

    monkeypatch.setattr(auth_service_module.action_token_svc, "get_token_status", AsyncMock(return_value=token_row))
    monkeypatch.setattr(auth_service_module.user_repo, "get_by_id", AsyncMock(return_value=user))

    response = await AuthService().get_action_token_status(FakeDB(), "invite-token")

    assert response.status == "valid"
    assert response.action_type == "invite_set_password"
    assert response.email == user.email
    assert response.full_name == user.full_name
