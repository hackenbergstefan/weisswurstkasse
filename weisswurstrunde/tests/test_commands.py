import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
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


class BackupTests(TransactionTestCase):
    def test_consistent_backup_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.sqlite3"
            call_command("backup_db", str(path), stdout=StringIO())
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(CommandError):
                call_command("backup_db", str(path))
