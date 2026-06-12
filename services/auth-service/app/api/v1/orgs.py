from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select

from app.config import settings
from app.database import get_db
from app.dependencies import (
    assert_tenant_access,
    require_any_authenticated,
    require_tenant_admin_or_above,
)
from app.models.auth import AuthActionToken, AuthActionType, UserRole
from app.repositories.org_repository import OrgRepository
from app.repositories.plant_repository import PlantRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import (
    CreatePlantRequest,
    CreateUserRequest,
    FeatureEntitlementsResponse,
    GenericMessageResponse,
    PlantResponse,
    UpdateEntitlementsRequest,
    UpdateUserRequest,
    UserResponse,
)
from app.services.action_token_service import action_token_svc
from app.services.auth_service import AuthService, pwd_ctx, token_svc
from services.shared.feature_entitlements import (
    BASELINE_FEATURES_BY_ROLE,
    build_feature_entitlement_state,
    validate_premium_grants,
    validate_role_feature_matrix,
)
from services.shared.tenant_context import TenantContext, build_internal_headers
from services.shared.tenant_guards import assert_plants_belong_to_tenant, assert_same_tenant

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])

org_repo = OrgRepository()
plant_repo = PlantRepository()
user_repo = UserRepository()
auth_svc = AuthService()
UTC = timezone.utc


def _now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _tenant_route_ctx(claims: dict, tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        user_id=str(claims.get("sub") or "unknown"),
        role=str(claims.get("role") or "anonymous"),
        plant_ids=[str(plant_id) for plant_id in (claims.get("plant_ids") or [])],
        is_super_admin=False,
    )


def _assert_manageable_user_for_role(*, caller_role: str, target_user, action: str) -> None:
    if caller_role == "org_admin" and target_user.role in {UserRole.ORG_ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ROLE_ESCALATION_FORBIDDEN",
                "message": f"Org admins cannot {action} org_admin or super_admin users.",
            },
        )


async def _get_tenant_scoped_user_or_404(db, user_id: str, tenant_id: str):
    user = await user_repo.get_by_id_for_tenant(db, user_id, tenant_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )
    return user


async def _assert_fresh_token(db, claims: dict) -> None:
    if db is None or not hasattr(db, "sync_session"):
        return
    await auth_svc.get_user_by_token_claims(db, claims)


async def _get_tenant_or_404(db, tenant_id: str):
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )
    return org


async def _get_plant_or_404(db, tenant_id: str, plant_id: str):
    plant = await plant_repo.get_by_id_for_tenant(db, tenant_id, plant_id)
    if plant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLANT_NOT_FOUND", "message": "Plant not found"},
        )
    return plant


async def _get_plant_device_count(tenant_id: str, plant_id: str) -> int:
    base_url = settings.DEVICE_SERVICE_BASE_URL.rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLANT_DELETE_GUARD_UNAVAILABLE",
                "message": "Plant dependency guard is not configured.",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base_url}/api/v1/devices/internal/plants/{plant_id}/device-count",
                headers=build_internal_headers("auth-service", tenant_id),
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLANT_DELETE_GUARD_UNAVAILABLE",
                "message": "Unable to verify attached devices right now. Please try again.",
            },
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLANT_DELETE_GUARD_UNAVAILABLE",
                "message": "Unable to verify attached devices right now. Please try again.",
            },
        ) from exc

    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLANT_NOT_FOUND", "message": "Plant not found"},
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLANT_DELETE_GUARD_UNAVAILABLE",
                "message": "Unable to verify attached devices right now. Please try again.",
            },
        )

    payload = response.json()
    count = payload.get("device_count")
    if not isinstance(count, int):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PLANT_DELETE_GUARD_UNAVAILABLE",
                "message": "Plant dependency guard returned an unexpected response.",
            },
        )
    return count


def _entitlements_response(org, role: str) -> FeatureEntitlementsResponse:
    state = build_feature_entitlement_state(
        role=role,
        premium_feature_grants=org.premium_feature_grants_json,
        role_feature_matrix=org.role_feature_matrix_json,
        entitlements_version=org.entitlements_version,
    )
    return FeatureEntitlementsResponse(
        premium_feature_grants=state.premium_feature_grants_list,
        role_feature_matrix=state.role_feature_matrix_list,
        baseline_features_by_role={key: list(value) for key, value in BASELINE_FEATURES_BY_ROLE.items()},
        effective_features_by_role=state.effective_features_by_role_list,
        available_features=list(state.available_features),
        entitlements_version=state.entitlements_version,
    )


async def _invite_state_by_user_id(db, users) -> dict[str, dict]:
    if not users:
        return {}

    user_ids = [user.id for user in users]
    now = _now_utc_naive()
    rows = await db.execute(
        select(
            AuthActionToken.user_id,
            func.max(AuthActionToken.expires_at).label("latest_invite_expires_at"),
            func.max(
                case(
                    (
                        (AuthActionToken.used_at.is_(None) & (AuthActionToken.expires_at > now)),
                        1,
                    ),
                    else_=0,
                )
            ).label("has_pending_invite"),
            func.count(AuthActionToken.id).label("invite_count"),
        ).where(
            AuthActionToken.user_id.in_(user_ids),
            AuthActionToken.action_type == AuthActionType.INVITE_SET_PASSWORD,
        ).group_by(AuthActionToken.user_id)
    )

    if rows is None:
        return {}

    state: dict[str, dict] = {}
    for row in rows:
        state[str(row.user_id)] = {
            "latest_invite_expires_at": row.latest_invite_expires_at,
            "has_pending_invite": bool(row.has_pending_invite),
            "has_invite_history": int(row.invite_count or 0) > 0,
        }
    return state


def _build_user_response(user, invite_state: dict | None = None) -> UserResponse:
    invite_state = invite_state or {}
    has_pending_invite = bool(invite_state.get("has_pending_invite"))
    has_invite_history = bool(invite_state.get("has_invite_history"))
    latest_invite_expires_at = invite_state.get("latest_invite_expires_at")
    never_activated = user.activated_at is None

    if user.is_active:
        lifecycle_state = "active"
        invite_status = "none"
    elif never_activated:
        if has_pending_invite:
            lifecycle_state = "invited"
            invite_status = "pending"
        elif has_invite_history:
            lifecycle_state = "invite_expired"
            invite_status = "expired"
        else:
            lifecycle_state = "deactivated"
            invite_status = "none"
    else:
        lifecycle_state = "deactivated"
        invite_status = "none"

    return UserResponse.model_validate(
        {
            **UserResponse.model_validate(user).model_dump(),
            "lifecycle_state": lifecycle_state,
            "invite_status": invite_status,
            "pending_invite_expires_at": latest_invite_expires_at if has_pending_invite else None,
            "can_resend_invite": (not user.is_active) and never_activated,
            "can_reactivate": (not user.is_active) and (not never_activated),
            "can_deactivate": bool(user.is_active),
        }
    )


@router.post("/{tenant_id}/plants", response_model=PlantResponse, status_code=status.HTTP_201_CREATED)
async def create_plant(
    tenant_id: str,
    body: CreatePlantRequest,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> PlantResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    await _get_tenant_or_404(db, tenant_id)
    await auth_svc.assert_org_active_for_write(db, tenant_id)

    plant = await plant_repo.create(db, tenant_id, body.name, body.location, body.timezone)
    return PlantResponse.model_validate(plant)


@router.get("/{tenant_id}/plants", response_model=list[PlantResponse], status_code=status.HTTP_200_OK)
async def list_plants(
    tenant_id: str,
    claims: dict = Depends(require_any_authenticated),
    db=Depends(get_db),
) -> list[PlantResponse]:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    plants = await plant_repo.list_by_tenant(db, tenant_id)
    return [PlantResponse.model_validate(plant) for plant in plants]


@router.post("/{tenant_id}/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    tenant_id: str,
    body: CreateUserRequest,
    claims: dict = Depends(require_any_authenticated),
    db=Depends(get_db),
) -> UserResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)
    caller_role = str(claims.get("role") or "")
    caller_plant_ids = {str(plant_id) for plant_id in (claims.get("plant_ids") or [])}

    if caller_role not in {"super_admin", "org_admin", "plant_manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FORBIDDEN",
                "message": "Only organization admins and plant managers can invite users.",
            },
        )

    if caller_role == "plant_manager" and body.role not in ("operator", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ROLE_ESCALATION_FORBIDDEN",
                "message": "Plant managers can only create operator or viewer users.",
            },
        )

    if caller_role == "org_admin" and body.role in ("super_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ROLE_ESCALATION_FORBIDDEN",
                "message": "Org admins cannot create org_admin or super_admin users.",
            },
        )

    if body.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "TENANT_ID_MISMATCH",
                "message": "tenant_id must match the path parameter.",
            },
        )

    user_role = UserRole(body.role)
    if caller_role == "org_admin" and user_role in {UserRole.ORG_ADMIN, UserRole.SUPER_ADMIN}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ROLE_ESCALATION_FORBIDDEN",
                "message": "Org admins cannot create org_admin or super_admin users.",
            },
        )

    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )
    await auth_svc.assert_org_active_for_write(db, tenant_id)

    validated_plant_ids: list[str] = []
    if user_role in {UserRole.PLANT_MANAGER, UserRole.OPERATOR, UserRole.VIEWER}:
        validated_plant_ids = list(dict.fromkeys(body.plant_ids))
        if not validated_plant_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INVALID_PLANT_IDS",
                    "message": "At least one plant must be selected.",
                },
            )
        if caller_role == "plant_manager" and len(validated_plant_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "INVALID_PLANT_IDS",
                    "message": "Plant managers must assign exactly one plant.",
                },
            )
        org_plants = await plant_repo.list_by_tenant(db, tenant_id)
        valid_plant_ids = {plant.id for plant in org_plants}
        if caller_role == "plant_manager":
            assert_plants_belong_to_tenant(validated_plant_ids, caller_plant_ids & valid_plant_ids, ctx)
        else:
            assert_plants_belong_to_tenant(validated_plant_ids, valid_plant_ids, ctx)
        await auth_svc.assert_plants_active_for_assignment(
            db,
            tenant_id=tenant_id,
            plant_ids=validated_plant_ids,
        )

    supplied_password = body.password.strip() if body.password else None

    existing = await user_repo.get_by_email(db, body.email)
    user = existing
    if user is None:
        user = await user_repo.create(
            db,
            email=body.email,
            hashed_password=pwd_ctx.hash(supplied_password or secrets.token_urlsafe(32)),
            role=user_role,
            tenant_id=body.tenant_id,
            full_name=body.full_name,
        )
    else:
        if user.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "EMAIL_TAKEN", "message": "Email already exists in another organization"},
            )
        if caller_role == "plant_manager" and user.role not in {UserRole.OPERATOR, UserRole.VIEWER}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ROLE_ESCALATION_FORBIDDEN",
                    "message": "Plant managers can only manage operator or viewer users.",
                },
            )
        if caller_role == "org_admin" and user.role in {UserRole.ORG_ADMIN, UserRole.SUPER_ADMIN}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "ROLE_ESCALATION_FORBIDDEN",
                    "message": "Org admins cannot manage org_admin or super_admin users.",
                },
            )
        if user.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "EMAIL_TAKEN", "message": "Email already exists"},
            )
        if user.activated_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "USER_DEACTIVATED_USE_REACTIVATE",
                    "message": "User is deactivated. Use reactivate instead of creating a new invite.",
                },
            )
        user.role = user_role
        user.full_name = body.full_name
        user.tenant_id = tenant_id

    user.is_active = supplied_password is not None
    if supplied_password is not None:
        user.hashed_password = pwd_ctx.hash(supplied_password)
        user.activated_at = _now_utc_naive()
        user.deactivated_at = None
    else:
        user.invited_at = _now_utc_naive()
        user.deactivated_at = None

    if validated_plant_ids:
        await user_repo.set_plant_access(db, user.id, validated_plant_ids)

    if supplied_password is None:
        await auth_svc.send_invitation(
            db,
            user=user,
            created_by_user_id=ctx.user_id,
            created_by_role=ctx.role,
            tenant_id=ctx.tenant_id,
        )

    invite_state = await _invite_state_by_user_id(db, [user])
    return _build_user_response(user, invite_state.get(user.id))


@router.get("/{tenant_id}/users", response_model=list[UserResponse], status_code=status.HTTP_200_OK)
async def list_users(
    tenant_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> list[UserResponse]:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    users = await user_repo.list_by_tenant(db, tenant_id)
    invite_state = await _invite_state_by_user_id(db, users)
    return [_build_user_response(user, invite_state.get(user.id)) for user in users]


@router.get("/{tenant_id}/entitlements", response_model=FeatureEntitlementsResponse, status_code=status.HTTP_200_OK)
async def get_entitlements(
    tenant_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> FeatureEntitlementsResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )

    feature_role = "org_admin" if claims["role"] == "super_admin" else claims["role"]
    return _entitlements_response(org, feature_role)


@router.put("/{tenant_id}/entitlements", response_model=FeatureEntitlementsResponse, status_code=status.HTTP_200_OK)
async def update_entitlements(
    tenant_id: str,
    body: UpdateEntitlementsRequest,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> FeatureEntitlementsResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    org = await org_repo.get_by_id(db, tenant_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORG_NOT_FOUND", "message": "Organization not found"},
        )

    caller_role = str(claims.get("role") or "")
    if caller_role == "org_admin":
        if body.premium_feature_grants is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FEATURE_SCOPE_DENIED",
                    "message": "Org admins cannot modify organisation-level premium grants.",
                },
            )
        if body.role_feature_matrix is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "ROLE_MATRIX_REQUIRED",
                    "message": "Org admins must submit a role feature matrix.",
                },
            )
    else:
        if body.role_feature_matrix is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FEATURE_SCOPE_DENIED",
                    "message": "Super admins manage organisation grants only.",
                },
            )
        if body.premium_feature_grants is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "PREMIUM_GRANTS_REQUIRED",
                    "message": "Premium feature grants must be provided.",
                },
            )

    if caller_role == "org_admin":
        validated_matrix = validate_role_feature_matrix(
            role_feature_matrix=body.role_feature_matrix,
            allowed_premium_features=org.premium_feature_grants_json,
            caller_role=caller_role,
        )
        org = await org_repo.update_entitlements(
            db,
            tenant_id,
            role_feature_matrix=validated_matrix,
        )
    else:
        validated_grants = validate_premium_grants(body.premium_feature_grants)
        org = await org_repo.update_entitlements(
            db,
            tenant_id,
            premium_feature_grants=validated_grants,
        )

    return _entitlements_response(org, "org_admin" if caller_role == "super_admin" else caller_role)


@router.put("/{tenant_id}/users/{user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK)
@router.patch("/{tenant_id}/users/{user_id}", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def update_user(
    tenant_id: str,
    user_id: str,
    body: UpdateUserRequest,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> UserResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)

    target_user = await _get_tenant_scoped_user_or_404(db, user_id, tenant_id)
    assert_same_tenant(ctx, target_user.tenant_id, "user", user_id)
    _assert_manageable_user_for_role(caller_role=str(claims.get("role") or ""), target_user=target_user, action="manage")
    await auth_svc.assert_org_active_for_write(db, tenant_id)

    if claims["role"] == "org_admin" and body.role in ("super_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ROLE_ESCALATION_FORBIDDEN",
                "message": "Org admins cannot create org_admin or super_admin users.",
            },
        )

    updates: dict = {}
    if body.full_name is not None:
        updates["full_name"] = body.full_name
    if body.role is not None:
        updates["role"] = UserRole(body.role)
    if body.is_active is not None:
        if body.is_active and target_user.activated_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "REACTIVATE_NOT_ALLOWED_PENDING_INVITE",
                    "message": "Pending or expired invites must use resend/reinvite.",
                },
            )
        updates["is_active"] = body.is_active
        updates["deactivated_at"] = None if body.is_active else _now_utc_naive()

    validated_plant_ids: list[str] | None = None
    if body.plant_ids is not None:
        validated_plant_ids = list(dict.fromkeys(body.plant_ids))
        org_plants = await plant_repo.list_by_tenant(db, tenant_id)
        valid_plant_ids = {plant.id for plant in org_plants}
        assert_plants_belong_to_tenant(validated_plant_ids, valid_plant_ids, ctx)
        await auth_svc.assert_plants_active_for_assignment(
            db,
            tenant_id=tenant_id,
            plant_ids=validated_plant_ids,
        )

    updated_user = await user_repo.update(db, user_id, updates)

    permissions_changed = validated_plant_ids is not None or "role" in updates or "is_active" in updates
    if validated_plant_ids is not None:
        await user_repo.set_plant_access(db, user_id, validated_plant_ids)

    if permissions_changed:
        if body.is_active is False and target_user.activated_at is None:
            await action_token_svc.invalidate_open_tokens(
                db,
                user_id=user_id,
                action_type=AuthActionType.INVITE_SET_PASSWORD,
            )
        await user_repo.increment_permissions_version(db, user_id)
        await token_svc.revoke_all_user_tokens(db, user_id)

    invite_state = await _invite_state_by_user_id(db, [updated_user])
    return _build_user_response(updated_user, invite_state.get(updated_user.id))


@router.get("/{tenant_id}/users/{user_id}/plant-access", status_code=status.HTTP_200_OK)
async def get_user_plant_access(
    tenant_id: str,
    user_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> dict:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)
    target_user = await _get_tenant_scoped_user_or_404(db, user_id, tenant_id)
    assert_same_tenant(ctx, target_user.tenant_id, "user", user_id)
    _assert_manageable_user_for_role(caller_role=str(claims.get("role") or ""), target_user=target_user, action="view plant access for")
    plant_ids = await user_repo.get_plant_ids(db, user_id)
    return {"plant_ids": plant_ids}


@router.post("/{tenant_id}/users/{user_id}/resend-invite", response_model=GenericMessageResponse, status_code=status.HTTP_200_OK)
async def resend_user_invite(
    tenant_id: str,
    user_id: str,
    claims: dict = Depends(require_any_authenticated),
    db=Depends(get_db),
) -> GenericMessageResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)
    caller_role = str(claims.get("role") or "")
    target_user = await _get_tenant_scoped_user_or_404(db, user_id, tenant_id)
    assert_same_tenant(ctx, target_user.tenant_id, "user", user_id)
    _assert_manageable_user_for_role(caller_role=caller_role, target_user=target_user, action="resend invites for")

    if target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVITE_NOT_PENDING", "message": "Only pending invite users can receive a resent invite."},
        )
    if target_user.activated_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "USER_DEACTIVATED_USE_REACTIVATE",
                "message": "This user was previously activated. Use reactivate instead of resend invite.",
            },
        )

    if caller_role not in {"super_admin", "org_admin", "plant_manager"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "You are not allowed to resend invites."},
        )

    if caller_role == "plant_manager":
        if target_user.role not in {UserRole.OPERATOR, UserRole.VIEWER}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "ROLE_ESCALATION_FORBIDDEN", "message": "Plant managers can only resend invites for operator or viewer users."},
            )
        caller_plant_ids = {str(plant_id) for plant_id in (claims.get("plant_ids") or [])}
        target_plant_ids = set(await user_repo.get_plant_ids(db, user_id))
        assert_plants_belong_to_tenant(list(target_plant_ids), caller_plant_ids, ctx)

    await auth_svc.assert_org_active_for_write(db, tenant_id)
    await auth_svc.resend_invitation(
        db,
        user=target_user,
        created_by_user_id=ctx.user_id,
        created_by_role=ctx.role,
        tenant_id=ctx.tenant_id,
    )
    return GenericMessageResponse(message="Invitation email resent.")


@router.patch("/{tenant_id}/users/{user_id}/deactivate", status_code=status.HTTP_200_OK)
async def deactivate_user(
    tenant_id: str,
    user_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> dict:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)
    target_user = await _get_tenant_scoped_user_or_404(db, user_id, tenant_id)
    assert_same_tenant(ctx, target_user.tenant_id, "user", user_id)
    _assert_manageable_user_for_role(caller_role=str(claims.get("role") or ""), target_user=target_user, action="deactivate")
    await auth_svc.assert_org_active_for_write(db, tenant_id)
    target_user.is_active = False
    target_user.deactivated_at = _now_utc_naive()
    if target_user.activated_at is None:
        await action_token_svc.invalidate_open_tokens(
            db,
            user_id=target_user.id,
            action_type=AuthActionType.INVITE_SET_PASSWORD,
        )
    await user_repo.increment_permissions_version(db, user_id)
    await token_svc.revoke_all_user_tokens(db, user_id)
    return {"message": "User deactivated"}


@router.patch("/{tenant_id}/users/{user_id}/reactivate", status_code=status.HTTP_200_OK)
async def reactivate_user(
    tenant_id: str,
    user_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> dict:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    ctx = _tenant_route_ctx(claims, tenant_id)
    target_user = await _get_tenant_scoped_user_or_404(db, user_id, tenant_id)
    assert_same_tenant(ctx, target_user.tenant_id, "user", user_id)
    _assert_manageable_user_for_role(caller_role=str(claims.get("role") or ""), target_user=target_user, action="reactivate")
    await auth_svc.assert_org_active_for_write(db, tenant_id)

    if target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "USER_ALREADY_ACTIVE", "message": "User is already active"},
        )
    if target_user.activated_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "REACTIVATE_NOT_ALLOWED_PENDING_INVITE",
                "message": "Pending or expired invites must use resend/reinvite.",
            },
        )

    target_user.is_active = True
    target_user.deactivated_at = None
    await user_repo.increment_permissions_version(db, user_id)
    await token_svc.revoke_all_user_tokens(db, user_id)
    return {"message": "User reactivated"}


@router.patch("/{tenant_id}/plants/{plant_id}/deactivate", response_model=PlantResponse, status_code=status.HTTP_200_OK)
async def deactivate_plant(
    tenant_id: str,
    plant_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> PlantResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    await auth_svc.assert_org_active_for_write(db, tenant_id)
    plant = await _get_plant_or_404(db, tenant_id, plant_id)
    if not plant.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PLANT_ALREADY_INACTIVE", "message": "Plant is already inactive"},
        )

    plant = await plant_repo.update(db, plant_id, {"is_active": False})
    return PlantResponse.model_validate(plant)


@router.patch("/{tenant_id}/plants/{plant_id}/reactivate", response_model=PlantResponse, status_code=status.HTTP_200_OK)
async def reactivate_plant(
    tenant_id: str,
    plant_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> PlantResponse:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    await auth_svc.assert_org_active_for_write(db, tenant_id)
    plant = await _get_plant_or_404(db, tenant_id, plant_id)
    if plant.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PLANT_ALREADY_ACTIVE", "message": "Plant is already active"},
        )

    plant = await plant_repo.update(db, plant_id, {"is_active": True})
    return PlantResponse.model_validate(plant)


@router.get("/{tenant_id}/plants/{plant_id}/delete-guard", status_code=status.HTTP_200_OK)
async def get_plant_delete_guard(
    tenant_id: str,
    plant_id: str,
    claims: dict = Depends(require_tenant_admin_or_above),
    db=Depends(get_db),
) -> dict:
    await _assert_fresh_token(db, claims)
    assert_tenant_access(claims, tenant_id)
    await _get_plant_or_404(db, tenant_id, plant_id)
    device_count = await _get_plant_device_count(tenant_id, plant_id)
    if device_count > 0:
        return {
            "can_delete": False,
            "device_count": device_count,
            "code": "PLANT_DELETE_BLOCKED_DEVICES_EXIST",
            "message": "Plant deletion is blocked because devices are still attached to this plant.",
        }

    return {
        "can_delete": True,
        "device_count": 0,
        "message": "This plant currently has no attached devices.",
    }
