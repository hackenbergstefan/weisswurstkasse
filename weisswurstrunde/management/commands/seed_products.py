from django.core.management.base import BaseCommand

from weisswurstrunde.models import EventType, Product


class Command(BaseCommand):
    help = (
        "Add example breakfast products without changing existing prices. Review prices before use."
    )

    def handle(self, *args, **options):
        for name, unit, price, event_type in [
            ("Wei\u00dfwurst", "St\u00fcck", 160, EventType.WEISSWURST),
            ("Breze", "St\u00fcck", 90, EventType.WEISSWURST),
            ("Leberkassemmel", "St\u00fcck", 200, EventType.LEBERKAESE),
        ]:
            Product.objects.get_or_create(
                name=name,
                defaults={"unit": unit, "price_cents": price, "event_type": event_type},
            )
        self.stdout.write(self.style.SUCCESS("Example products ready. Review prices in Sortiment."))
