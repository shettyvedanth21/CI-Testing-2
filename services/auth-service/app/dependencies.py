from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.database import get_db
from app.models.auth import UserRole
from app.services.token_service import TokenService
from services.shared.tenant_context import normalize_tenant_id

token_svc = TokenService()


async def get_token_claims(request: Request, db=Depends(get_db)) -> dict:
    from app.repositories.user_repository import UserRepository
    from app.services.auth_service import AuthService

    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_AUTH_TOKEN", "message": "Authentication token missing"},
        )

    token = auth_header.split(" ", 1)[1].strip()
    claims = await token_svc.decode_access_token_async(token)
    auth_svc = AuthService()
    user_repo = UserRepository()
    user = await auth_svc.get_user_by_token_claims(db, claims)
    if user.role in {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN}:
        plant_ids: list[str] = []
    else:
        plant_ids = await user_repo.get_plant_ids(db, user.id)

    claims["sub"] = user.id
    claims["role"] = user.role.value
    claims["tenant_id"] = normalize_tenant_id(user.tenant_id)
    claims["plant_ids"] = [str(plant_id) for plant_id in plant_ids]
    claims["email"] = user.email
    claims["full_name"] = user.full_name
    request.state.token_claims = claims
    return claims


async def require_super_admin(claims: dict = Depends(get_token_claims)) -> dict:
    if claims.get("role") != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Super admin access required"},
        )
    return claims


async def require_tenant_admin_or_above(claims: dict = Depends(get_token_claims)) -> dict:
    if claims.get("role") not in {"super_admin", "org_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Tenant admin access required"},
        )
    return claims


async def require_any_authenticated(claims: dict = Depends(get_token_claims)) -> dict:
    return claims


def get_claim_tenant_id(claims: dict) -> str | None:
    tenant_id = normalize_tenant_id(claims.get("tenant_id"))
    claims["tenant_id"] = tenant_id
    return tenant_id


def assert_tenant_access(claims: dict, tenant_id: str) -> None:
    if claims.get("role") == "super_admin":
        return
    claim_tenant_id = get_claim_tenant_id(claims)
    if claim_tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ORG_ACCESS_DENIED", "message": "Access to this organization is forbidden"},
        )


def assert_plant_access(claims: dict, plant_id: str) -> None:
    if claims.get("role") in {"super_admin", "org_admin"}:
        return
    if plant_id not in (claims.get("plant_ids") or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PLANT_ACCESS_DENIED", "message": "Access to this plant is forbidden"},
        )
