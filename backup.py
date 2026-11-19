#!/usr/bin/env python3
"""Consistent backup of the Story Studio database.

    python3 backup.py                      # writes backups/studio-YYYYmmdd-HHMMSS.db, keeps the newest 14
    python3 backup.py --dir /var/backups/studio --keep 30

Uses SQLite's online backup API, so it is safe while the server is running (copying studio.db with
cp can produce a broken file because of the write-ahead log). The copy is checked with
PRAGMA integrity_check before older backups are deleted.
Restore: stop the server, then copy a backup over studio.db (and delete studio.db-wal / studio.db-shm).
Reads the same STUDIO_DB variable as server.py.
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent


def backup(src, dest_dir, keep):
    src, dest_dir = Path(src), Path(dest_dir)
    if not src.is_file():
        sys.exit(f"Database not found: {src}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(dest_dir, 0o700)
    dest = dest_dir / time.strftime("studio-%Y%m%d-%H%M%S.db")
    n = 1
    while dest.exists():  # two backups in the same second
        dest = dest_dir / time.strftime(f"studio-%Y%m%d-%H%M%S-{n}.db")
        n += 1
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
        # The source runs in WAL mode and backup() copies that setting. Switch the copy to a plain rollback
        # journal so the backup is one self-contained file with no -wal/-shm companions.
        d.execute("PRAGMA journal_mode=DELETE")
    finally:
        d.close()
        s.close()
    os.chmod(dest, 0o600)
    c = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
    try:
        result = c.execute("PRAGMA integrity_check").fetchone()[0]
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        c.close()
    if result != "ok":
        dest.unlink()
        sys.exit(f"Backup failed its integrity check ({result}); it was deleted and nothing was rotated.")
    old = sorted(dest_dir.glob("studio-*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink()
        for ext in ("-wal", "-shm", "-journal"):
            Path(str(f) + ext).unlink(missing_ok=True)
    print(f"Backup OK: {dest} ({dest.stat().st_size} bytes, {users} users). Kept {min(keep, len(list(dest_dir.glob('studio-*.db'))))} backups.")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(ROOT / "backups"), help="where to write backups (default ./backups)")
    ap.add_argument("--keep", type=int, default=14, help="how many backups to keep (0 = keep all)")
    a = ap.parse_args()
    backup(os.environ.get("STUDIO_DB", str(ROOT / "studio.db")), a.dir, a.keep)
