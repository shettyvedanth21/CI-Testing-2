from __future__ import annotations

import logging
from datetime import datetime, timezone
from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth import AuthActionToken, AuthActionType, User, UserRole
from app.repositories.org_repository import OrgRepository
from app.repositories.plant_repository import PlantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import ActionTokenStatusResponse, TokenResponse
from app.services.action_token_service import action_token_svc
from app.services.mailer_service import mailer_svc
from app.services.token_service import TokenService

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

user_repo = UserRepository()
org_repo = OrgRepository()
plant_repo = PlantRepository()
token_svc = TokenService()

UTC = timezone.utc
logger = logging.getLogger("auth-service.auth")


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AuthService:
    async def assert_org_active_for_write(self, db: AsyncSession, tenant_id: str) -> None:
        org = await org_repo.get_by_id(db, tenant_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
            )
        if not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ORG_SUSPENDED",
                    "message": "Organization is suspended. New invites and resource creation are blocked.",
                },
            )

    async def assert_plants_active_for_assignment(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        plant_ids: list[str],
    ) -> None:
        unique_plant_ids = list(dict.fromkeys(plant_ids))
        if not unique_plant_ids:
            return

        plants = await plant_repo.list_by_ids_for_tenant(db, tenant_id, unique_plant_ids)
        plant_by_id = {plant.id: plant for plant in plants}
        missing_ids = [plant_id for plant_id in unique_plant_ids if plant_id not in plant_by_id]
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INVALID_PLANT_IDS",
                    "message": "One or more selected plants are not available in this organization.",
                    "rejected_ids": missing_ids,
                },
            )

        inactive_ids = [plant_id for plant_id, plant in plant_by_id.items() if not plant.is_active]
        if inactive_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PLANT_INACTIVE",
                    "message": "Inactive plants cannot be used for new assignments or onboarding.",
                    "plant_ids": inactive_ids,
                },
            )

    def _build_frontend_link(self, path: str, token: str) -> str:
        base = settings.FRONTEND_BASE_URL.rstrip("/")
        return f"{base}{path}?token={token}"

    def _validate_password_inputs(self, password: str, confirm_password: str) -> None:
        if password != confirm_password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "PASSWORD_MISMATCH", "message": "Passwords do not match"},
            )

    async def send_invitation(
        self,
        db: AsyncSession,
        *,
        user: User,
        created_by_user_id: str | None,
        created_by_role: str | None,
        tenant_id: str | None,
    ) -> None:
        if tenant_id is not None:
            await self.assert_org_active_for_write(db, tenant_id)
        user.invited_at = _now_utc_naive()
        # Reinviting a never-activated account re-opens onboarding lifecycle.
        if user.activated_at is None:
            user.deactivated_at = None
        await action_token_svc.invalidate_open_tokens(
            db,
            user_id=user.id,
            action_type=AuthActionType.INVITE_SET_PASSWORD,
        )
        raw_token = await action_token_svc.create_token(
            db,
            user_id=user.id,
            action_type=AuthActionType.INVITE_SET_PASSWORD,
            expires_in_minutes=settings.INVITE_TOKEN_EXPIRE_MINUTES,
            created_by_user_id=created_by_user_id,
            created_by_role=created_by_role,
            tenant_id=tenant_id,
            metadata={"email": user.email},
        )
        try:
            await mailer_svc.send_invite_email(
                recipient=user.email,
                full_name=user.full_name,
                invite_link=self._build_frontend_link("/accept-invite", raw_token),
            )
        except Exception:
            logger.exception("Failed to deliver invitation email", extra={"recipient": user.email, "user_id": user.id})

    async def resend_invitation(
        self,
        db: AsyncSession,
        *,
        user: User,
        created_by_user_id: str | None,
        created_by_role: str | None,
        tenant_id: str | None,
    ) -> None:
        await self.send_invitation(
            db,
            user=user,
            created_by_user_id=created_by_user_id,
            created_by_role=created_by_role,
            tenant_id=tenant_id,
        )

    async def accept_invitation(
        self,
        db: AsyncSession,
        *,
        token: str,
        password: str,
        confirm_password: str,
    ) -> None:
        self._validate_password_inputs(password, confirm_password)
        token_row = await action_token_svc.consume_token(
            db,
            raw_token=token,
            expected_action_type=AuthActionType.INVITE_SET_PASSWORD,
        )
        user = await user_repo.get_by_id(db, token_row.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ACTION_TOKEN", "message": "Invalid or expired link"},
            )
        await self._assert_org_active(db, user)
        user.hashed_password = pwd_ctx.hash(password)
        user.is_active = True
        if user.activated_at is None:
            user.activated_at = _now_utc_naive()
        user.deactivated_at = None
        user.updated_at = _now_utc_naive()
        await token_svc.revoke_all_user_tokens(db, user.id)
        await db.flush()

    async def request_password_reset(self, db: AsyncSession, *, email: str) -> None:
        user = await user_repo.get_by_email(db, email)
        if user is None or not user.is_active:
            return
        try:
            await self._assert_org_active(db, user)
        except HTTPException:
            return
        await action_token_svc.invalidate_open_tokens(
            db,
            user_id=user.id,
            action_type=AuthActionType.PASSWORD_RESET,
        )
        raw_token = await action_token_svc.create_token(
            db,
            user_id=user.id,
            action_type=AuthActionType.PASSWORD_RESET,
            expires_in_minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES,
            created_by_user_id=user.id,
            created_by_role=user.role.value,
            tenant_id=user.tenant_id,
            metadata={"email": user.email},
        )
        try:
            await mailer_svc.send_password_reset_email(
                recipient=user.email,
                full_name=user.full_name,
                reset_link=self._build_frontend_link("/reset-password", raw_token),
            )
        except Exception:
            logger.exception("Failed to deliver password reset email", extra={"recipient": user.email, "user_id": user.id})

    async def reset_password(
        self,
        db: AsyncSession,
        *,
        token: str,
        password: str,
        confirm_password: str,
    ) -> None:
        self._validate_password_inputs(password, confirm_password)
        token_row = await action_token_svc.consume_token(
            db,
            raw_token=token,
            expected_action_type=AuthActionType.PASSWORD_RESET,
        )
        user = await user_repo.get_by_id(db, token_row.user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_ACTION_TOKEN", "message": "Invalid or expired link"},
            )
        await self._assert_org_active(db, user)
        user.hashed_password = pwd_ctx.hash(password)
        user.updated_at = datetime.now(UTC)
        await token_svc.revoke_all_user_tokens(db, user.id)
        await db.flush()

    async def get_action_token_status(self, db: AsyncSession, token: str) -> ActionTokenStatusResponse:
        token_row = await action_token_svc.get_token_status(db, token)
        if token_row is None:
            return ActionTokenStatusResponse(status="invalid")

        if token_row.used_at is not None:
            return ActionTokenStatusResponse(status="used", action_type=token_row.action_type.value)

        if _as_utc_datetime(token_row.expires_at) <= datetime.now(UTC):
            return ActionTokenStatusResponse(status="expired", action_type=token_row.action_type.value)

        user = await user_repo.get_by_id(db, token_row.user_id)
        return ActionTokenStatusResponse(
            status="valid",
            action_type=token_row.action_type.value,
            email=user.email if user else None,
            full_name=user.full_name if user else None,
        )

    async def _assert_org_active(self, db: AsyncSession, user: User) -> None:
        tenant_id = user.tenant_id
        if tenant_id is None:
            return

        org = await org_repo.get_by_id(db, tenant_id)
        if org is None or not org.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ORG_SUSPENDED", "message": "Organization is suspended"},
            )

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[User, TokenResponse]:
        generic_message = "Invalid credentials"
        user = await user_repo.get_by_email(db, email)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_CREDENTIALS", "message": generic_message},
            )

        now_utc = datetime.now(timezone.utc)
        pending_invite_result = await db.execute(
            select(AuthActionToken.id).where(
                AuthActionToken.user_id == user.id,
                AuthActionToken.action_type == AuthActionType.INVITE_SET_PASSWORD,
                AuthActionToken.used_at.is_(None),
                AuthActionToken.expires_at > now_utc,
            )
        )
        pending_invite = (
            pending_invite_result.scalar_one_or_none()
            if pending_invite_result is not None and hasattr(pending_invite_result, "scalar_one_or_none")
            else None
        )
        if pending_invite is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PASSWORD_SETUP_REQUIRED", "message": "Complete password setup from your invite email"},
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ACCOUNT_DISABLED", "message": "Account is disabled"},
            )

        if not pwd_ctx.verify(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_CREDENTIALS", "message": generic_message},
            )

        await self._assert_org_active(db, user)
        login_at = _now_utc_naive()
        user.last_login_at = login_at
        user.updated_at = login_at
        await db.flush()

        tenant_entitlements_version = None
        tenant_id = user.tenant_id
        if tenant_id is not None:
            tenant = await org_repo.get_by_id(db, tenant_id)
            if tenant is not None:
                tenant_entitlements_version = int(getattr(tenant, "entitlements_version", 0) or 0)

        if user.role in {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN}:
            plant_ids = []
        else:
            plant_ids = await user_repo.get_plant_ids(db, user.id)

        raw_refresh_token, token_hash = token_svc.generate_refresh_token_pair()
        access_token = await token_svc.create_access_token_async(
            user,
            plant_ids,
            tenant_entitlements_version=tenant_entitlements_version,
        )
        await token_svc.store_refresh_token(db, user.id, token_hash)

        return (
            user,
            TokenResponse(
                access_token=access_token,
                refresh_token=raw_refresh_token,
                expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            ),
        )

    async def refresh(self, db: AsyncSession, raw_refresh_token: str) -> TokenResponse:
        refresh_token_row = await token_svc.validate_refresh_token(db, raw_refresh_token)
        user = await user_repo.get_by_id(db, refresh_token_row.user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        await self._assert_org_active(db, user)

        await token_svc.revoke_refresh_token(db, raw_refresh_token)

        tenant_entitlements_version = None
        tenant_id = user.tenant_id
        if tenant_id is not None:
            tenant = await org_repo.get_by_id(db, tenant_id)
            if tenant is not None:
                tenant_entitlements_version = int(getattr(tenant, "entitlements_version", 0) or 0)

        if user.role in {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN}:
            plant_ids = []
        else:
            plant_ids = await user_repo.get_plant_ids(db, user.id)

        raw_new_refresh_token, new_token_hash = token_svc.generate_refresh_token_pair()
        access_token = await token_svc.create_access_token_async(
            user,
            plant_ids,
            tenant_entitlements_version=tenant_entitlements_version,
        )
        await token_svc.store_refresh_token(db, user.id, new_token_hash)

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_new_refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def logout(self, db: AsyncSession, raw_refresh_token: str | None, access_claims: dict | None = None) -> None:
        if access_claims is not None:
            await token_svc.revoke_access_token_from_claims_async(access_claims)
        try:
            if raw_refresh_token:
                await token_svc.revoke_refresh_token(db, raw_refresh_token)
        except Exception:
            return

    async def get_user_by_token_claims(self, db: AsyncSession, claims: dict) -> User:
        user_id = claims.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        token_version = claims.get("permissions_version")
        if token_version is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        user = await user_repo.get_by_id(db, user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        current_version = getattr(user, "permissions_version", 0) or 0
        if current_version != token_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
            )

        tenant_id = user.tenant_id
        if tenant_id is not None:
            tenant_version = claims.get("tenant_entitlements_version")
            tenant = await org_repo.get_by_id(db, tenant_id)
            if tenant is None or not tenant.is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
                )
            current_tenant_version = getattr(tenant, "entitlements_version", 0) or 0
            if tenant_version is None or int(tenant_version) != int(current_tenant_version):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={"code": "INVALID_TOKEN", "message": "Invalid token"},
                )

        await self._assert_org_active(db, user)
        return user
