from django.core.management.base import BaseCommand

from weisswurstrunde.models import Product


class Command(BaseCommand):
    help = (
        "Add example breakfast products without changing existing prices. Review prices before use."
    )

    def handle(self, *args, **options):
        for name, unit, price in [
            ("Wei\u00dfwurst", "St\u00fcck", 160),
            ("Brezel", "St\u00fcck", 90),
            ("S\u00fc\u00dfer Senf", "Portion", 30),
            ("Wei\u00dfbier", "Flasche", 220),
        ]:
            Product.objects.get_or_create(name=name, defaults={"unit": unit, "price_cents": price})
        self.stdout.write(self.style.SUCCESS("Example products ready. Review prices in Sortiment."))
