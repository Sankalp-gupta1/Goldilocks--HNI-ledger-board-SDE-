"""Consistent, owner-readable SQLite backup. Run where the database is mounted."""

import argparse
import os
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("database", type=Path)
parser.add_argument("destination", type=Path)
args = parser.parse_args()
if not args.database.is_file():
    parser.error("The source database does not exist.")
if args.destination.exists():
    parser.error("Choose a new destination; backups are never overwritten.")
args.destination.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(args.destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
try:
    source = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(args.destination)
    source.backup(destination)
    assert destination.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
finally:
    if "source" in locals():
        source.close()
    if "destination" in locals():
        destination.close()
print("Backup completed and integrity checked. Store it in protected durable storage.")
