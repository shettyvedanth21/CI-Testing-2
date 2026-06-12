"""
Run Alembic migrations to head before starting the service.
Exit non-zero on any failure — prevents starting with wrong schema.
"""
import logging
import subprocess
import sys

logging.basicConfig(level="INFO")
logger = logging.getLogger("migration_guard")


def run_migrations():
    logger.info("Running alembic upgrade head...")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        logger.info(result.stdout)
    if result.returncode != 0:
        logger.error(f"Migration failed:\n{result.stderr}")
        sys.exit(1)
    logger.info("Migrations complete.")


if __name__ == "__main__":
    run_migrations()

