
import asyncio
from app.core.database import AsyncSessionLocal
from app.models.models import User, Project, SubDivision, Budget, AuditLog, Notification
from sqlalchemy import delete, select

async def purge_data():
    username = "tester@example.com"
    project_name = "E2E Test Project"
    
    async with AsyncSessionLocal() as db:
        async with db.begin():
            # Find the user id
            user_res = await db.execute(select(User).where(User.username == username))
            user = user_res.scalar_one_or_none()
            user_id = user.id if user else None
            
            # Find project id
            proj_res = await db.execute(select(Project).where(Project.name == project_name))
            project = proj_res.scalar_one_or_none()
            project_id = project.id if project else None
            
            # Hard delete project (will cascade to subdivisions and budgets if FKs are set to delete orphan, 
            # but let's be explicit)
            if project_id:
                # 1. Budgets for subdivisions of this project
                sd_res = await db.execute(select(SubDivision.id).where(SubDivision.project_id == project_id))
                sd_ids = sd_res.scalars().all()
                if sd_ids:
                    await db.execute(delete(Budget).where(Budget.sub_division_id.in_(sd_ids)))
                    await db.execute(delete(SubDivision).where(SubDivision.id.in_(sd_ids)))
                
                await db.execute(delete(Project).where(Project.id == project_id))
                print(f"Purged project: {project_name}")

            # Delete Audit Logs for this user
            if user_id:
                await db.execute(delete(AuditLog).where(AuditLog.user_id == user_id))
                await db.execute(delete(Notification).where(Notification.user_id == user_id))
                await db.execute(delete(User).where(User.id == user_id))
                print(f"Purged user: {username}")
            
            print("Purge complete.")

if __name__ == "__main__":
    asyncio.run(purge_data())
