import tempfile
from importlib import import_module
from io import StringIO
from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from weisswurstrunde.models import Event, EventType, Product


class CommandTests(TestCase):
    def test_generation_seeding_and_reconciliation(self):
        output = StringIO()
        call_command("seed_products", stdout=output)
        call_command("seed_products", stdout=output)
        self.assertEqual(Product.objects.count(), 3)
        self.assertFalse(
            Product.objects.filter(name__in=["S\u00fc\u00dfer Senf", "Wei\u00dfbier"]).exists()
        )
        product = Product.objects.get(event_type=EventType.LEBERKAESE)
        self.assertEqual((product.name, product.price_cents), ("Leberkassemmel", 200))
        product.price_cents = 250
        product.save()
        call_command("seed_products", stdout=output)
        product.refresh_from_db()
        self.assertEqual(product.price_cents, 250)
        call_command("generate_events", stdout=output)
        call_command("generate_events", stdout=output)
        self.assertGreaterEqual(Event.objects.count(), 8)
        call_command("reconcile", stdout=output)
        self.assertIn("All ledger records reconcile", output.getvalue())
        call_command("worker", once=True, stdout=output)

    def test_retired_products_remain_in_database_and_seed_does_not_reactivate(self):
        mustard = Product.objects.create(name="S\u00fc\u00dfer Senf", price_cents=30)
        beer = Product.objects.create(name="Wei\u00dfbier", price_cents=220)
        migration = import_module("weisswurstrunde.migrations.0005_retire_mustard_and_beer")
        migration.retire_products(apps, connection.schema_editor())
        call_command("seed_products", stdout=StringIO())
        for product in [mustard, beer]:
            product.refresh_from_db()
            self.assertFalse(product.active)
        self.assertEqual((mustard.price_cents, beer.price_cents), (30, 220))
        self.assertEqual(Product.objects.filter(active=True).count(), 3)


class BackupTests(TransactionTestCase):
    def test_consistent_backup_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.sqlite3"
            call_command("backup_db", str(path), stdout=StringIO())
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CommandError):
                call_command("backup_db", str(path))
