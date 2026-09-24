from django.core.management.base import BaseCommand

from weisswurstrunde.services import generate_events


class Command(BaseCommand):
    help = "Create upcoming weekly events and missing default orders, idempotently."

    def handle(self, *args, **options):
        count = generate_events()
        self.stdout.write(self.style.SUCCESS(f"Created {count} events."))
