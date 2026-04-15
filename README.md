# Budget Allocator & Tracker — Backend

> **Enterprise-grade budget planning and allocation API** built with FastAPI, SQLAlchemy 2.0 (async), PostgreSQL, and TOTP-based MFA.

---

## Prerequisites

| Dependency | Version |
|---|---|
| Python | 3.10 + |
| PostgreSQL | 13 + |
| Git | any |

---

## 1. Clone & Environment Setup

```bash
# Navigate to project directory
cd budget_allocator

# Create and activate virtual environment
python -m venv venv

# Windows
.\venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 2. Configure Environment Variables

Create a `.env` file by copying the template and filling in your credentials:

```bash
# Copy the template (Windows)
copy .env.example .env
```

Edit `.env` with your values:

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/budget_tracker
SECRET_KEY=your-super-secret-key-at-least-32-chars
DEBUG=true
APP_NAME=Budget Allocator
APP_VERSION=2.0
```

---

## 3. Starting the Server

> [!IMPORTANT]
> **Always use `run_server.py`** as your entrypoint. Do NOT run `uvicorn` or `alembic` manually. The launcher handles the full initialization pipeline in the correct, safe order.

### Default (Development)

```bash
python run_server.py --reload
```

### Production

```bash
python run_server.py --host 0.0.0.0 --port 8000
```

### Custom Port

```bash
python run_server.py --port 8080 --reload
```

### What the launcher does automatically
Every time you run `run_server.py`, the following pipeline executes **before** the web server starts:

```
1. [Migration]   alembic upgrade head  →  Ensures DB schema is current
2. [Seeding]     Idempotently injects:
                   • Default Admin User (only on first run)
                   • All 6 Business Unit / Family combinations
                   • Global Rate Card multipliers
3. [Launch]      uvicorn app.main:app  →  Starts the API server
```

---

## 4. First-Time MFA Setup (Admin Account)

When starting the server on a **fresh database**, the console will print a one-time setup token:

```
| ======================================================================
| [!] INITIAL SETUP REQUIRED
| ======================================================================
| System created default admin user: tejasbhat2001@gmail.com
| Complete MFA Setup via API (valid for 72h):
| Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
| ======================================================================
```

**Enroll MFA using that token:**

```http
POST http://127.0.0.1:8000/api/v1/auth/setup
Authorization: Bearer <token_from_console>

{
  "new_password": "YourNewSecurePassword!"
}
```

The response will contain a `totp_provisioning_uri`. Scan the QR code (or enter the `secret=` value manually) into **Google Authenticator** or **Authy**.

---

## 5. Logging In

```http
POST http://127.0.0.1:8000/api/v1/auth/login

{
  "username": "tejasbhat2001@gmail.com",
  "password": "YourNewSecurePassword!",
  "totp_code": "123456"
}
```

---

## 6. API Documentation

| URL | Tool |
|---|---|
| http://127.0.0.1:8000/docs | Swagger UI (interactive) |
| http://127.0.0.1:8000/redoc | ReDoc (readable) |

---

## 7. Project Hierarchy

```
Business Unit  (e.g., CPE, NPI, CSS)
└── Family     (e.g., INKJET, LASERJET)
    └── Team   (e.g., CPE Team Alpha)
        └── Run    (e.g., Q2 2026 Test Run)
            └── Budget  (The full calculated financial plan)
```

---

## 8. Key Scripts Reference

| Script | Purpose |
|---|---|
| `run_server.py` | ✅ **Primary entrypoint.** Runs migration → seed → server |
| `scripts/init_db.py` | Database-only init (for CI/CD pipelines) |
| `scripts/seed_admin.py` | Legacy standalone admin seeder |

---

## 9. Architecture Overview

| Component | Technology |
|---|---|
| Web Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 Async |
| Database | PostgreSQL (via asyncpg) |
| Schema Migrations | Alembic |
| Authentication | JWT + TOTP (MFA) |
| Background Jobs | APScheduler (in-process) |
| Excel Export | openpyxl |
| Password Hashing | argon2-cffi |

---

## 10. Advanced: Running Init Without Starting Server

This is useful in a **CI/CD pipeline** (e.g., GitHub Actions, Docker `CMD`, Kubernetes Init Containers):

```bash
# Only migrate and seed — do NOT start the web server
python scripts/init_db.py
```

Then start the server independently:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```


Step 1 → alembic upgrade head       (apply any new migrations)
Step 2 → seed_initial_data()        (families, rate cards — idempotent)
Step 3 → seed_admin()               (only acts on first-ever fresh DB)
Step 4 → uvicorn app.main:app       (server starts)
