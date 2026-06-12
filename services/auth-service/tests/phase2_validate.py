import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "mysql+aiomysql://energy:energy@localhost:3306/ai_factoryops"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-32-characters-long"

from app.models.auth import UserRole
from app.services.token_service import TokenService
from app.schemas.auth import (
    LoginRequest, CreateTenantRequest, CreateUserRequest,
    TokenResponse, UserResponse, TenantResponse
)

async def main():
    errors = []

    # Test 1: TokenService creates and decodes a valid access token
    try:
        from app.models.auth import User
        fake_user = User()
        fake_user.id = "test-user-id"
        fake_user.email = "test@example.com"
        fake_user.tenant_id = "test-tenant-id"
        fake_user.role = UserRole.ORG_ADMIN
        fake_user.permissions_version = 0
        fake_user.full_name = "Test User"

        svc = TokenService()
        token = svc.create_access_token(fake_user, ["plant-1"])
        assert isinstance(token, str) and len(token) > 50, "Token must be a non-trivial string"
        claims = svc.decode_access_token(token)
        assert claims["sub"] == "test-user-id"
        assert claims["tenant_id"] == "test-tenant-id"
        assert claims["role"] == "org_admin"
        assert claims["plant_ids"] == ["plant-1"]
        assert claims["permissions_version"] == 0
        assert claims["type"] == "access"
        print("PASS: TokenService.create_access_token and decode_access_token")
    except Exception as e:
        errors.append(f"FAIL: TokenService token creation/decode — {e}")

    # Test 2: generate_refresh_token_pair returns two distinct strings
    try:
        svc = TokenService()
        raw, hashed = svc.generate_refresh_token_pair()
        assert len(raw) > 40, "Raw token must be long"
        assert len(hashed) == 64, "Hash must be 64-char hex (SHA-256)"
        assert raw != hashed, "Raw and hash must differ"
        # Verify it's deterministic
        import hashlib
        assert hashlib.sha256(raw.encode()).hexdigest() == hashed
        print("PASS: TokenService.generate_refresh_token_pair")
    except Exception as e:
        errors.append(f"FAIL: generate_refresh_token_pair — {e}")

    # Test 3: decode_access_token raises 401 on garbage input
    try:
        from fastapi import HTTPException
        svc = TokenService()
        try:
            svc.decode_access_token("not.a.real.token")
            errors.append("FAIL: decode_access_token should raise on invalid token")
        except HTTPException as e:
            assert e.status_code == 401
            print("PASS: decode_access_token raises 401 on invalid token")
    except Exception as e:
        errors.append(f"FAIL: decode_access_token error handling — {e}")

    # Test 4: Schemas validate correctly
    try:
        req = LoginRequest(email="user@example.com", password="secret")
        assert req.email == "user@example.com"

        try:
            CreateTenantRequest(name="Test", slug="INVALID SLUG!")
            errors.append("FAIL: slug validator should reject invalid slug")
        except Exception:
            pass  # Expected

        valid = CreateTenantRequest(name="Test Corp", slug="test-corp")
        assert valid.slug == "test-corp"
        created_user = CreateUserRequest(
            email="tenant@example.com",
            full_name="Tenant User",
            role="viewer",
            tenant_id="tenant-a",
            plant_ids=[],
        )
        aliased_user = CreateUserRequest(
            email="org@example.com",
            full_name="Org User",
            role="viewer",
            tenant_id="tenant-a",
            plant_ids=[],
        )
        assert created_user.tenant_id == "tenant-a"
        print("PASS: Pydantic schemas validate correctly")
    except Exception as e:
        errors.append(f"FAIL: Pydantic schema validation — {e}")

    # Test 5: pwd_ctx hashes and verifies password
    try:
        from passlib.context import CryptContext
        ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        hashed = ctx.hash("mypassword123")
        assert ctx.verify("mypassword123", hashed)
        assert not ctx.verify("wrongpassword", hashed)
        print("PASS: passlib bcrypt hash and verify")
    except Exception as e:
        errors.append(f"FAIL: passlib — {e}")

    if errors:
        print("\nVALIDATION FAILED:")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)
    else:
        print("\nAll Phase 2 validations passed.")

asyncio.run(main())
