"""Database migration script — runs all module migrations in order.

Per-module migration directories (§7.3):
Each module owns its migration history inside its own schema.
This makes extraction nearly free: migration history travels with the module.

Usage:
    python scripts/migrate.py upgrade
    python scripts/migrate.py downgrade
    python scripts/migrate.py create --message "add index to household_id"
"""
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

MODULES = [
    "identity",
    "inbound",
    "extraction",
    "operations",
    "connectors",
    "skills",
    "notification",
    "briefing",
    "support",
]


def run_alembic(module: str, command: str, *args: str) -> None:
    """Run an alembic command for a specific module."""
    env_path = ROOT / "app" / "modules" / module / "migrations" / "env.py"
    if not env_path.exists():
        print(f"  Skipping {module} — no migrations directory")
        return

    cmd = [
        "alembic",
        "-c", str(ROOT / "alembic.ini"),
        "--name", module,
        command,
        *args,
    ]
    print(f"  [{module}] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR in {module}: {result.stderr}")
        sys.exit(1)
    if result.stdout:
        print(result.stdout)


async def create_schemas() -> None:
    """Create all module schemas before running migrations."""
    from app.platform.db import AsyncSessionFactory, create_schemas
    async with AsyncSessionFactory() as session:
        await create_schemas(session)
    print("All schemas created.")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate.py <upgrade|downgrade|heads>")
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    if command == "upgrade" and not args:
        args = ["head"]

    print("Creating schemas...")
    asyncio.run(create_schemas())

    print(f"\nRunning '{command}' for all modules...")
    for module in MODULES:
        run_alembic(module, command, *args)

    print("\nMigrations complete.")


if __name__ == "__main__":
    main()
