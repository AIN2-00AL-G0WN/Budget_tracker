import urllib.request
import urllib.error
import json
import pyotp
import time
import sys

BASE_URL = "http://127.0.0.1:8000/api/v1/auth"

import os
import asyncio
sys.path.insert(0, os.path.abspath('.'))
from app.core.database import AsyncSessionLocal
from app.models.models import User
from app.core.security import hash_password
from sqlalchemy import select

async def prepare_db_user():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == "testuser2@example.com"))
        user = result.scalar_one_or_none()
        if user:
            await db.delete(user)
            await db.commit()
            
        totp_secret = pyotp.random_base32()
        user = User(
            username="testuser2@example.com",
            hashed_password=hash_password("InitialP@ss123!"),
            is_active=True,
            is_admin=False,
            requires_password_change=False,
            totp_secret=totp_secret,
            token_version=0
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return totp_secret

def post_json(url, data, headers=None):
    if headers is None: headers = {}
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, body

def run_tests():
    totp_secret = asyncio.run(prepare_db_user())
    totp = pyotp.TOTP(totp_secret)
    
    print("\n--- Testing Login ---")
    code = totp.now()
    status, body = post_json(f"{BASE_URL}/login", {
        "username": "testuser2@example.com",
        "password": "InitialP@ss123!",
        "totp_code": code
    })
    print(status, body)
    if status != 200: sys.exit(1)
    
    token = body["access_token"]
    
    print("\n--- Testing Change Password ---")
    time.sleep(1)
    new_code = totp.now()
    if new_code == code:
        time.sleep(30)
        new_code = totp.now()
        
    status, body = post_json(f"{BASE_URL}/change-password", {
        "current_password": "InitialP@ss123!",
        "new_password": "ChangedP@ss123!",
        "confirm_password": "ChangedP@ss123!",
        "totp_code": new_code
    }, headers={"Authorization": f"Bearer {token}"})
    print(status, body)
    if status != 200: sys.exit(1)

    print("\n--- Testing Login with Changed Password ---")
    time.sleep(1)
    login_code = totp.now()
    if login_code == new_code:
        time.sleep(30)
        login_code = totp.now()

    status, body = post_json(f"{BASE_URL}/login", {
        "username": "testuser2@example.com",
        "password": "ChangedP@ss123!",
        "totp_code": login_code
    })
    print(status, body)
    if status != 200: sys.exit(1)

    print("\n--- Testing Forgot Password ---")
    time.sleep(1)
    forgot_code = totp.now()
    if forgot_code == login_code:
        time.sleep(30)
        forgot_code = totp.now()
        
    status, body = post_json(f"{BASE_URL}/forgot-password", {
        "username": "testuser2@example.com",
        "new_password": "ForgotP@ss123!",
        "confirm_password": "ForgotP@ss123!",
        "totp_code": forgot_code
    })
    print(status, body)
    if status != 200: sys.exit(1)

    print("\n--- Verify Forgot Password changes ---")
    time.sleep(2)  # To avoid TOTP reuse
    verify_code = totp.now()
    if verify_code == forgot_code:
        time.sleep(30)
        verify_code = totp.now()
        
    status, body = post_json(f"{BASE_URL}/login", {
        "username": "testuser2@example.com",
        "password": "ForgotP@ss123!",
        "totp_code": verify_code
    })
    print(status, body)
    if status != 200: sys.exit(1)

    print("\nALL PASSED")
    
if __name__ == "__main__":
    run_tests()
