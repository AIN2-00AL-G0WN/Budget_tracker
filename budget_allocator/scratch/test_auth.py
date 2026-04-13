import sys
import os
import asyncio
from fastapi.testclient import TestClient

# Add project root to sys.path
sys.path.insert(0, os.path.abspath('.'))

from app.main import app
from app.core.database import Base, engine, AsyncSessionLocal
from app.models.models import User
from app.core.security import hash_password, create_token
from sqlalchemy.ext.asyncio import AsyncSession
import pyotp

client = TestClient(app)

async def setup_test_db():
    pass


    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        result = await db.execute(select(User).where(User.username == "testuser@example.com"))
        existing = result.scalar_one_or_none()
        if existing:
            await db.delete(existing)
            await db.commit()

        # Create user
        totp_secret = pyotp.random_base32()
        user = User(
            username="testuser@example.com",
            hashed_password=hash_password("CurrentP@ss123!"),
            is_active=True,
            is_admin=False,
            requires_password_change=False,
            totp_secret=totp_secret,
            token_version=0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id, totp_secret

def run_tests():
    # Setup async db
    loop = asyncio.get_event_loop()
    user_id, totp_secret = loop.run_until_complete(setup_test_db())

    totp = pyotp.TOTP(totp_secret)
    code = totp.now()

    # 1. Login
    res = client.post("/api/v1/auth/login", json={
        "username": "testuser@example.com",
        "password": "CurrentP@ss123!",
        "totp_code": code
    })
    print("Login:", res.status_code, res.json())
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]

    # 2. Change password
    import time
    time.sleep(1) # to ensure different token
    new_code = totp.now()
    if new_code == code:
        time.sleep(30)
        new_code = totp.now()
        
    res = client.post("/api/v1/auth/change-password", headers={
        "Authorization": f"Bearer {token}"
    }, json={
        "current_password": "CurrentP@ss123!",
        "new_password": "NewP@ssword123!",
        "confirm_password": "NewP@ssword123!",
        "totp_code": new_code
    })
    print("Change Password:", res.status_code, res.json())
    assert res.status_code == 200, res.text

    # 3. Forgot password
    time.sleep(1)
    forgot_code = totp.now()
    if forgot_code == new_code:
        time.sleep(30)
        forgot_code = totp.now()
        
    res = client.post("/api/v1/auth/forgot-password", json={
        "username": "testuser@example.com",
        "new_password": "ForgotP@ssword123!",
        "confirm_password": "ForgotP@ssword123!",
        "totp_code": forgot_code
    })
    print("Forgot Password:", res.status_code, res.json())
    assert res.status_code == 200, res.text

    print("ALL TESTS PASSED")

if __name__ == "__main__":
    run_tests()
