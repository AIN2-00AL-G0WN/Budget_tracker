
import asyncio
import uuid
from app.core.database import AsyncSessionLocal
from app.models.models import User
from app.core.security import hash_password
from app.crud import crud_user
from sqlalchemy import delete

async def inject_user():
    username = "tester@example.com"
    password = "Rdl@12345"
    async with AsyncSessionLocal() as db:
        async with db.begin():
            # Clean up existing if any
            await db.execute(delete(User).where(User.username == username))
            
            # Create user
            await crud_user.create_user(
                db,
                username=username,
                hashed_password=hash_password(password),
                is_admin=True,
                is_active=True,
                requires_password_change=False
            )
            print(f"Successfully injected user: {username}")

if __name__ == "__main__":
    asyncio.run(inject_user())
