from django.core.management.base import BaseCommand, CommandError

from weisswurstrunde.paypal import sync_mailbox
from weisswurstrunde.services import reconcile


class Command(BaseCommand):
    help = "Verify ledger against orders and optionally reconcile PayPal mail."

    def add_arguments(self, parser):
        parser.add_argument(
            "--paypal",
            action="store_true",
            help="PayPal-Eingänge und -Ausgänge per IMAP abgleichen",
        )

    def handle(self, *args, **options):
        errors = []
        if options["paypal"]:
            try:
                sync_mailbox()
            except Exception as error:
                errors.append(f"PayPal-IMAP: {type(error).__name__}")
        errors.extend(reconcile())
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS("All ledger records reconcile."))
