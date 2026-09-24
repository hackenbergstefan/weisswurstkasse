from django.core.management.base import BaseCommand, CommandError

from weisswurstrunde.models import PayPalPayment
from weisswurstrunde.paypal import sync_payment
from weisswurstrunde.services import reconcile


class Command(BaseCommand):
    help = "Verify ledger against orders and PayPal records, optionally refreshing PayPal."

    def add_arguments(self, parser):
        parser.add_argument("--paypal", action="store_true")

    def handle(self, *args, **options):
        errors = []
        if options["paypal"]:
            for payment in PayPalPayment.objects.exclude(order_id=None):
                try:
                    sync_payment(payment.pk, capture=True)
                except Exception as error:
                    errors.append(f"PayPal {payment.pk}: {type(error).__name__}")
        errors.extend(reconcile())
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS("All ledger records reconcile."))
