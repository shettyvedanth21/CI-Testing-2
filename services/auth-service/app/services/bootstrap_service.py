from __future__ import annotations

from datetime import datetime, timezone

from passlib.context import CryptContext
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth import (
    TENANT_ID_SEQUENCE_WIDTH,
    Organization,
    Plant,
    TenantIdSequence,
    User,
    UserRole,
)
from app.services.tenant_id_service import TENANT_ID_PREFIX
from services.shared.feature_entitlements import DEFAULT_ROLE_DELEGATIONS, normalize_feature_keys

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
UTC = timezone.utc


async def ensure_bootstrap_super_admin(db: AsyncSession) -> bool:
    """Create or update the configured bootstrap super-admin identity from env."""
    if not settings.BOOTSTRAP_SUPER_ADMIN_ENABLED:
        return False

    missing = [
        key
        for key, value in {
            "BOOTSTRAP_SUPER_ADMIN_EMAIL": settings.BOOTSTRAP_SUPER_ADMIN_EMAIL.strip(),
            "BOOTSTRAP_SUPER_ADMIN_PASSWORD": settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD,
            "BOOTSTRAP_SUPER_ADMIN_FULL_NAME": settings.BOOTSTRAP_SUPER_ADMIN_FULL_NAME.strip(),
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"STARTUP BLOCKED: Missing bootstrap super-admin settings: {missing}")

    existing_email = await db.execute(
        select(User).where(User.email == settings.BOOTSTRAP_SUPER_ADMIN_EMAIL).limit(1)
    )
    bootstrap_user = existing_email.scalar_one_or_none()
    if bootstrap_user is not None and bootstrap_user.role != UserRole.SUPER_ADMIN:
        raise RuntimeError(
            "STARTUP BLOCKED: BOOTSTRAP_SUPER_ADMIN_EMAIL is already used by a non-super-admin user."
        )

    if bootstrap_user is None:
        db.add(
            User(
                email=settings.BOOTSTRAP_SUPER_ADMIN_EMAIL,
                hashed_password=pwd_ctx.hash(settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD),
                full_name=settings.BOOTSTRAP_SUPER_ADMIN_FULL_NAME,
                role=UserRole.SUPER_ADMIN,
                tenant_id=None,
                is_active=True,
                activated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )
    else:
        bootstrap_user.hashed_password = pwd_ctx.hash(settings.BOOTSTRAP_SUPER_ADMIN_PASSWORD)
        bootstrap_user.full_name = settings.BOOTSTRAP_SUPER_ADMIN_FULL_NAME
        bootstrap_user.role = UserRole.SUPER_ADMIN
        bootstrap_user.tenant_id = None
        bootstrap_user.is_active = True
        bootstrap_user.activated_at = datetime.now(UTC).replace(tzinfo=None)
        bootstrap_user.deactivated_at = None

    await db.commit()
    return bootstrap_user is None


def _extract_tenant_sequence(tenant_id: str) -> int:
    if not tenant_id.startswith(TENANT_ID_PREFIX):
        raise RuntimeError(
            f"STARTUP BLOCKED: LOCAL_BOOTSTRAP_TENANT_ID must start with {TENANT_ID_PREFIX!r}."
        )
    try:
        return int(tenant_id[len(TENANT_ID_PREFIX):])
    except ValueError as exc:
        raise RuntimeError("STARTUP BLOCKED: LOCAL_BOOTSTRAP_TENANT_ID must end with digits.") from exc


def _extract_existing_tenant_sequence(tenant_id: str | None) -> int | None:
    normalized = str(tenant_id or "").strip()
    if not normalized.startswith(TENANT_ID_PREFIX):
        return None
    suffix = normalized[len(TENANT_ID_PREFIX):]
    if len(suffix) != TENANT_ID_SEQUENCE_WIDTH or not suffix.isdigit():
        return None
    return int(suffix)


async def ensure_tenant_allocator_state(db: AsyncSession) -> bool:
    """Ensure the canonical tenant ID allocator row exists and is advanced enough."""

    connection = await db.connection()

    def _has_required_tables(sync_connection) -> bool:
        inspector = inspect(sync_connection)
        return inspector.has_table("organizations") and inspector.has_table("tenant_id_sequences")

    if not await connection.run_sync(_has_required_tables):
        return False

    org_ids = (await db.execute(select(Organization.id))).scalars().all()
    max_existing_sequence = 0
    for org_id in org_ids:
        sequence_value = _extract_existing_tenant_sequence(org_id)
        if sequence_value is None:
            continue
        max_existing_sequence = max(max_existing_sequence, sequence_value)

    desired_next_value = max_existing_sequence + 1 if max_existing_sequence else 1
    allocator = await db.scalar(
        select(TenantIdSequence).where(TenantIdSequence.prefix == TENANT_ID_PREFIX).limit(1)
    )
    allocator_updated = False
    if allocator is None:
        db.add(TenantIdSequence(prefix=TENANT_ID_PREFIX, next_value=desired_next_value))
        allocator_updated = True
    elif int(allocator.next_value) < desired_next_value:
        allocator.next_value = desired_next_value
        allocator_updated = True

    if allocator_updated:
        await db.commit()
    return allocator_updated


async def ensure_local_bootstrap_state(db: AsyncSession) -> dict[str, bool]:
    if not settings.LOCAL_BOOTSTRAP_ENABLED:
        return {
            "tenant_created": False,
            "tenant_updated": False,
            "plant_created": False,
            "plant_updated": False,
            "allocator_updated": False,
        }

    tenant_id = settings.LOCAL_BOOTSTRAP_TENANT_ID.strip()
    tenant_slug = settings.LOCAL_BOOTSTRAP_TENANT_SLUG.strip()
    plant_id = settings.LOCAL_BOOTSTRAP_PLANT_ID.strip()
    required_features = normalize_feature_keys(settings.local_bootstrap_premium_features)
    role_feature_matrix = {
        role: list(features) for role, features in DEFAULT_ROLE_DELEGATIONS.items()
    }
    now = datetime.now(UTC)

    existing_org = await db.scalar(
        select(Organization).where(Organization.id == tenant_id).limit(1)
    )
    conflicting_slug = await db.scalar(
        select(Organization)
        .where(Organization.slug == tenant_slug, Organization.id != tenant_id)
        .limit(1)
    )
    if conflicting_slug is not None:
        raise RuntimeError(
            "STARTUP BLOCKED: LOCAL_BOOTSTRAP_TENANT_SLUG is already used by another tenant."
        )

    tenant_created = False
    tenant_updated = False
    if existing_org is None:
        existing_org = Organization(
            id=tenant_id,
            name=settings.LOCAL_BOOTSTRAP_TENANT_NAME,
            slug=tenant_slug,
            is_active=True,
            premium_feature_grants_json=required_features,
            role_feature_matrix_json=role_feature_matrix,
            entitlements_version=1 if required_features else 0,
            created_at=now,
            updated_at=now,
        )
        db.add(existing_org)
        tenant_created = True
    else:
        expected_features = sorted(set(required_features) | set(existing_org.premium_feature_grants_json or []))
        expected_matrix = existing_org.role_feature_matrix_json or role_feature_matrix

        field_updates = {
            "name": settings.LOCAL_BOOTSTRAP_TENANT_NAME,
            "slug": tenant_slug,
            "is_active": True,
            "premium_feature_grants_json": expected_features,
            "role_feature_matrix_json": expected_matrix,
        }
        for field_name, value in field_updates.items():
            if getattr(existing_org, field_name) != value:
                setattr(existing_org, field_name, value)
                tenant_updated = True

        if tenant_updated:
            existing_org.entitlements_version = (existing_org.entitlements_version or 0) + 1
            existing_org.updated_at = now

    allocator_updated = False
    desired_next_value = _extract_tenant_sequence(tenant_id) + 1
    allocator = await db.scalar(
        select(TenantIdSequence).where(TenantIdSequence.prefix == TENANT_ID_PREFIX).limit(1)
    )
    if allocator is None:
        db.add(TenantIdSequence(prefix=TENANT_ID_PREFIX, next_value=desired_next_value))
        allocator_updated = True
    elif int(allocator.next_value) < desired_next_value:
        allocator.next_value = desired_next_value
        allocator_updated = True

    existing_plant = await db.scalar(
        select(Plant).where(Plant.id == plant_id).limit(1)
    )
    plant_created = False
    plant_updated = False
    if existing_plant is None:
        db.add(
            Plant(
                id=plant_id,
                tenant_id=tenant_id,
                name=settings.LOCAL_BOOTSTRAP_PLANT_NAME,
                location=settings.LOCAL_BOOTSTRAP_PLANT_LOCATION,
                timezone=settings.LOCAL_BOOTSTRAP_PLANT_TIMEZONE,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        plant_created = True
    else:
        if existing_plant.tenant_id != tenant_id:
            raise RuntimeError(
                "STARTUP BLOCKED: LOCAL_BOOTSTRAP_PLANT_ID already exists under a different tenant."
            )
        plant_updates = {
            "name": settings.LOCAL_BOOTSTRAP_PLANT_NAME,
            "location": settings.LOCAL_BOOTSTRAP_PLANT_LOCATION,
            "timezone": settings.LOCAL_BOOTSTRAP_PLANT_TIMEZONE,
            "is_active": True,
        }
        for field_name, value in plant_updates.items():
            if getattr(existing_plant, field_name) != value:
                setattr(existing_plant, field_name, value)
                plant_updated = True
        if plant_updated:
            existing_plant.updated_at = now

    await db.commit()
    return {
        "tenant_created": tenant_created,
        "tenant_updated": tenant_updated,
        "plant_created": plant_created,
        "plant_updated": plant_updated,
        "allocator_updated": allocator_updated,
    }
