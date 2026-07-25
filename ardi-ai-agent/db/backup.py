import os
import shutil
import logging
import datetime
from config import DATABASE_URL

logger = logging.getLogger(__name__)

BACKUP_DIR = "db_backups"


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


async def backup_database() -> str | None:
    """Create a timestamped backup of the database. Returns backup path or None."""
    _ensure_backup_dir()
    try:
        if "sqlite" in DATABASE_URL:
            db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
            if not db_path:
                db_path = "ardi.db"
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{ts}.db")
            shutil.copy2(db_path, backup_path)
            logger.info("Database backed up to %s", backup_path)
            return backup_path
        logger.warning("Auto-backup only supports SQLite. Use pg_dump for PostgreSQL.")
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
