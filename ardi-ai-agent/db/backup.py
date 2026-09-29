import os
import shutil
import asyncio
import logging
import datetime
import subprocess
from urllib.parse import urlparse
from config import DATABASE_URL

logger = logging.getLogger(__name__)

BACKUP_DIR = "db_backups"


async def _upload_offsite(backup_path: str) -> str | None:
    """Copy a finished local backup to R2 (never raises — local backup is the source of truth)."""
    try:
        from storage import upload_backup_file, prune_remote_backups
        with open(backup_path, "rb") as f:
            data = f.read()
        ctype = "application/sql" if backup_path.endswith(".sql") else "application/octet-stream"
        key = await asyncio.to_thread(
            upload_backup_file, data, os.path.basename(backup_path), ctype
        )
        if key:
            await asyncio.to_thread(prune_remote_backups)
            logger.info("Offsite backup complete: %s", key)
        return key
    except Exception as e:
        logger.warning("Offsite backup skipped: %s", e)
        return None


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


async def backup_database() -> str | None:
    """Create a timestamped backup of the database. Returns backup path or None."""
    _ensure_backup_dir()
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if "sqlite" in DATABASE_URL:
            # Handle sqlite+aiosqlite:///./ardi_agent.db, sqlite:///... etc.
            db_path = DATABASE_URL.split(":///", 1)[-1] if ":///" in DATABASE_URL else DATABASE_URL
            if not db_path or db_path == DATABASE_URL:
                db_path = "ardi_agent.db"
            if not os.path.isfile(db_path):
                # Fall back to legacy name used by older releases.
                alt = "ardi.db" if os.path.basename(db_path) != "ardi.db" else "ardi_agent.db"
                db_path = alt if os.path.isfile(alt) else db_path
            if not os.path.isfile(db_path):
                logger.error("SQLite file not found: %s", db_path)
                return None
            backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.db")
            shutil.copy2(db_path, backup_path)
            logger.info("Database backed up to %s", backup_path)
            await _upload_offsite(backup_path)
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
        await _upload_offsite(backup_path)
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
