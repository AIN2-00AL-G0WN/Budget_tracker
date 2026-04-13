
import asyncio
import httpx
import uuid
from app.main import app
from app.services.audit_logger import register_audit_listeners
from decimal import Decimal

BASE_URL = "http://test"

async def run_e2e_test():
    # Manually register listeners for the test context
    register_audit_listeners()
    results = []
    
    async with httpx.AsyncClient(app=app, base_url=BASE_URL) as client:
        # 1. Login
        print("--- Step 1: Login ---")
        login_payload = {
            "username": "tester@example.com",
            "password": "Rdl@12345",
            "totp_code": "000000"
        }
        r = await client.post("/api/v1/auth/login", json=login_payload)
        if r.status_code == 200:
            token = r.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            results.append(("Login", "Pass"))
            print("Login successful.")
        else:
            results.append(("Login", f"Fail ({r.status_code}: {r.text})"))
            print(f"Login failed: {r.text}")
            return results

        # 2. Project Initialization
        print("\n--- Step 2: Project Initialization ---")
        project_payload = {"name": "E2E Test Project", "status": "ACTIVE"}
        r = await client.post("/api/v1/projects", json=project_payload, headers=headers)
        if r.status_code == 201:
            project_id = r.json()["id"]
            results.append(("Project Creation", "Pass"))
            print(f"Project created: {project_id}")
        else:
            results.append(("Project Creation", f"Fail ({r.status_code}: {r.text})"))
            print(f"Project creation failed: {r.text}")
            return results

        # 3. Phase Definition
        print("\n--- Step 3: Phase Definition ---")
        subdivision_payload = {"name": "Validation Phase", "status": "PLANNED"}
        r = await client.post(f"/api/v1/projects/{project_id}/subdivisions", json=subdivision_payload, headers=headers)
        if r.status_code == 201:
            sd_id = r.json()["id"]
            results.append(("SubDivision Creation", "Pass"))
            print(f"SubDivision created: {sd_id}")
        else:
            results.append(("SubDivision Creation", f"Fail ({r.status_code}: {r.text})"))
            print(f"SubDivision creation failed: {r.text}")
            return results

        # 4. Budget Calculation Verification
        print("\n--- Step 4: Budget Calculation Verification ---")
        budget_payload = {
            "sub_division_id": sd_id,
            "tc_count": 130,
            "duration_in_days": 5
        }
        r = await client.post("/api/v1/budgets", json=budget_payload, headers=headers)
        if r.status_code == 201:
            budget_data = r.json()
            budget_id = budget_data["id"]
            total = budget_data["total_budget"]
            manual_hc = budget_data["manual_hc"]
            automation_hc = budget_data["automation_hc"]
            
            # Check integer types for HC
            hc_is_int = isinstance(manual_hc, int) and isinstance(automation_hc, int)
            
            print(f"Budget calculated: Total=${total:.2f}, Manual HC={manual_hc}, Automation HC={automation_hc}")
            
            if 3000 < total < 4000 and hc_is_int:
                results.append(("Budget Calculation", "Pass"))
                print("Calculation validation passed.")
            else:
                results.append(("Budget Calculation", f"Fail (Total=${total}, HC Int={hc_is_int})"))
                print(f"Calculation validation failed: Total={total}, HC Int={hc_is_int}")
        else:
            results.append(("Budget Calculation", f"Fail ({r.status_code}: {r.text})"))
            print(f"Budget creation failed: {r.text}")
            return results

        # 5. Override & Audit Validation
        print("\n--- Step 5: Override & Audit Validation ---")
        patch_payload = {
            "tc_count": 130, # Same inputs
            "duration_in_days": 5,
            "project_manager_pct_override": 0.2
        }
        r = await client.patch(f"/api/v1/budgets/{budget_id}", json=patch_payload, headers=headers)
        if r.status_code == 200:
            results.append(("Budget Override", "Pass"))
            print("Budget override applied.")
            
            # Check Audit Log
            r_audit = await client.get("/api/v1/admin/audit-logs", headers=headers)
            if r_audit.status_code == 200:
                logs = r_audit.json()
                # Find log for this budget
                found = any(l["entity_id"] == budget_id and l["action"] == "UPDATE" for l in logs)
                if found:
                    results.append(("Audit Log Verification", "Pass"))
                    print("Audit log for UPDATE found.")
                else:
                    results.append(("Audit Log Verification", "Fail (Log not found)"))
                    print("Audit log for UPDATE not found.")
            else:
                results.append(("Audit Log Verification", f"Fail (Status {r_audit.status_code})"))
                print(f"Failed to fetch audit logs: {r_audit.status_code}")
        else:
            results.append(("Budget Override", f"Fail ({r.status_code}: {r.text})"))
            print(f"Budget override failed: {r.text}")

        # 6. Roll-up Analytics
        print("\n--- Step 6: Roll-up Analytics ---")
        # Project Summary
        r_sum = await client.get(f"/api/v1/projects/{project_id}/summary", headers=headers)
        if r_sum.status_code == 200:
            if r_sum.json()["total_budget"] > 0:
                results.append(("Project Summary Roll-up", "Pass"))
                print("Project summary roll-up verified.")
            else:
                results.append(("Project Summary Roll-up", "Fail (Sum is 0)"))
                print("Project summary roll-up returned 0.")
        else:
            results.append(("Project Summary Roll-up", f"Fail ({r_sum.status_code})"))
            print(f"Project summary failed: {r_sum.status_code}")

        # Global Summary
        r_glob = await client.get("/api/v1/analytics/global-summary", headers=headers)
        if r_glob.status_code == 200:
            if r_glob.json()["total_budget"] > 0:
                results.append(("Global Summary Roll-up", "Pass"))
                print("Global summary roll-up verified.")
            else:
                results.append(("Global Summary Roll-up", "Fail (Sum is 0)"))
                print("Global summary roll-up returned 0.")
        else:
            results.append(("Global Summary Roll-up", f"Fail ({r_glob.status_code})"))
            print(f"Global summary failed: {r_glob.status_code}")

        # 7. Workflow Lock Test
        print("\n--- Step 7: Workflow Lock Test ---")
        # Mark as COMPLETED
        lock_payload = {"status": "COMPLETED"}
        r_lock = await client.patch(f"/api/v1/projects/subdivisions/{sd_id}", json=lock_payload, headers=headers)
        if r_lock.status_code == 200:
            # Try to patch budget again
            r_fail = await client.patch(f"/api/v1/budgets/{budget_id}", json=patch_payload, headers=headers)
            if r_fail.status_code == 403:
                results.append(("Workflow Lock (Budget)", "Pass"))
                print("Workflow lock verified (received 403 on locked budget).")
            else:
                results.append(("Workflow Lock (Budget)", f"Fail (Expected 403, got {r_fail.status_code})"))
                print(f"Workflow lock failed: Expected 403, got {r_fail.status_code}")
        else:
            results.append(("Workflow Lock (Status Update)", f"Fail ({r_lock.status_code})"))
            print(f"Failed to update status to COMPLETED: {r_lock.status_code}")

    print("\n" + "="*30)
    print("FINAL REPORT")
    print("="*30)
    for test, res in results:
        print(f"{test:<30}: {res}")
    print("="*30)
    
    return results

if __name__ == "__main__":
    asyncio.run(run_e2e_test())
