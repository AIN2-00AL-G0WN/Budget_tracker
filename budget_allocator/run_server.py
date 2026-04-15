"""
run_server.py
-------------
Enterprise Application Entrypoint.

This script enforces the industry-standard initialization pipeline:
  1. Runs Alembic migrations (idempotent — safe to run on every boot).
  2. Seeds the database with foundational data (admin user, families, rate cards).
  3. Only after successful initialization does it launch the Uvicorn ASGI server.

Usage (from budget_allocator/ directory):
    python run_server.py                     # Production
    python run_server.py --reload            # Development (hot reload)
    python run_server.py --host 0.0.0.0      # Expose to network
    python run_server.py --port 8080         # Custom port
"""
from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import subprocess
import sys

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Ensure project root is on sys.path for `app.*` imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("Launcher")

BANNER = r"""
╔══════════════════════════════════════════════╗
║       Budget Allocator & Tracker  v2         ║
║       Enterprise Initialization Pipeline     ║
╚══════════════════════════════════════════════╝
"""


def run_init_pipeline() -> bool:
    """Execute the database initialization script as a subprocess.
    Returns True if successful, False on failure."""
    logger.info("Starting Database Initialization...")
    result = subprocess.run(
        [sys.executable, "scripts/init_db.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    if result.returncode != 0:
        logger.error("Database Initialization FAILED. Aborting server startup.")
        return False
    logger.info("Database Initialization completed successfully.")
    return True


def start_uvicorn(host: str, port: int, reload: bool) -> None:
    """Launch the Uvicorn ASGI server."""
    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")

    logger.info("Launching Uvicorn server at http://%s:%d", host, port)
    logger.info("API Docs: http://%s:%d/docs", host, port)
    subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    print(BANNER)

    parser = argparse.ArgumentParser(description="Budget Allocator Server Launcher")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable hot reload (development only)")
    parser.add_argument("--skip-init", action="store_true", help="Skip DB init (not recommended)")
    args = parser.parse_args()

    if not args.skip_init:
        success = run_init_pipeline()
        if not success:
            sys.exit(1)
    else:
        logger.warning("Skipping DB initialization (--skip-init flag set). Ensure DB is ready.")

    start_uvicorn(host=args.host, port=args.port, reload=args.reload)
