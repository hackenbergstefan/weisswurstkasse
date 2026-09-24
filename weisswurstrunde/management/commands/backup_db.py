import os
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Create a consistent SQLite backup, including ledger and PayPal references."

    def add_arguments(self, parser):
        parser.add_argument("destination")

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("Use pg_dump for PostgreSQL backups.")
        target = Path(options["destination"]).resolve()
        if target.exists():
            raise CommandError("Destination already exists; refusing to overwrite.")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        connection.ensure_connection()
        try:
            with sqlite3.connect(target) as backup:
                connection.connection.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise CommandError("Backup integrity check failed.")
        except Exception:
            target.unlink(missing_ok=True)
            raise
        self.stdout.write(self.style.SUCCESS(f"Backup created: {target}"))
