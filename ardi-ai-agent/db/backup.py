import os
import shutil
import logging
import datetime
import subprocess
from urllib.parse import urlparse
from config import DATABASE_URL

logger = logging.getLogger(__name__)

BACKUP_DIR = "db_backups"


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


async def backup_database() -> str | None:
    """Create a timestamped backup of the database. Returns backup path or None."""
    _ensure_backup_dir()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if "sqlite" in DATABASE_URL:
            db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
            if not db_path:
                db_path = "ardi.db"
            backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.db")
            shutil.copy2(db_path, backup_path)
            logger.info("Database backed up to %s", backup_path)
            return backup_path

        parsed = urlparse(DATABASE_URL.replace("+asyncpg", ""))
        backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.sql")
        env = os.environ.copy()
        env["PGPASSWORD"] = parsed.password or ""
        cmd = [
            "pg_dump",
            "--host", parsed.hostname or "localhost",
            "--port", str(parsed.port or 5432),
            "--username", parsed.username or "postgres",
            "--dbname", parsed.path.lstrip("/"),
            "--file", backup_path,
            "--no-owner",
            "--no-acl",
        ]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("pg_dump failed: %s", result.stderr)
            return None
        logger.info("Database backed up to %s", backup_path)
        return backup_path
    except FileNotFoundError:
        logger.error("pg_dump not found. Install PostgreSQL client tools or use psql.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("pg_dump timed out after 120s")
        return None
    except Exception as e:
        logger.error("Backup failed: %s", e)
        return None


async def prune_backups(keep: int = 7):
    """Remove old backups, keeping only the most recent `keep`."""
    _ensure_backup_dir()
    try:
        files = sorted(
            [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith("backup_")],
            key=os.path.getmtime,
        )
        while len(files) > keep:
            old = files.pop(0)
            os.remove(old)
            logger.info("Pruned old backup: %s", old)
    except Exception as e:
        logger.error("Backup pruning failed: %s", e)
