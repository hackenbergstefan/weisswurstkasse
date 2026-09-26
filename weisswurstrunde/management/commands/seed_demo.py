import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from weisswurstrunde.models import Event, EventType, Product, User
from weisswurstrunde.services import manual_payment, save_defaults, save_order


class Command(BaseCommand):
    help = "Create a small, repeatable example dataset for local development."

    demo_users = [
        ("anna.demo@example.org", "Anna", False),
        ("ben.demo@example.org", "Ben", False),
        ("admin.demo@example.org", "Demo-Admin", True),
    ]

    def handle(self, *args, **options):
        self.call_command("seed_products")
        self.call_command("generate_events")
        users = []
        for email, name, is_admin in self.demo_users:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "name": name,
                    "paypal_email": email,
                    "is_admin": is_admin,
                },
            )
            if created:
                user.set_password("demo")
                user.save(update_fields=["password"])
            elif is_admin and not user.is_admin:
                user.is_admin = True
                user.save(update_fields=["is_admin"])
            users.append(user)

        weisswurst = Product.objects.get(name="Wei\u00dfwurst", active=True)
        brezel = Product.objects.get(name="Breze", active=True)
        leberkas = Product.objects.get(name="Leberkassemmel", active=True)
        weisswurst_event = Event.objects.filter(
            event_type=EventType.WEISSWURST,
            date__gte=timezone.localdate(),
            deadline__gt=timezone.now(),
        ).first()
        leberkas_event = Event.objects.filter(
            event_type=EventType.LEBERKAESE,
            date__gte=timezone.localdate(),
            deadline__gt=timezone.now(),
        ).first()
        if not weisswurst_event or not leberkas_event:
            self.stderr.write(self.style.ERROR("No upcoming demo events found."))
            return

        for index, user in enumerate(users[:2]):
            order = user.orders.get_or_create(event=weisswurst_event)[0]
            save_order(
                order.pk,
                {weisswurst.pk: 2 - index, brezel.pk: 1},
                user,
            )
            leberkas_order = user.orders.get_or_create(event=leberkas_event)[0]
            save_order(leberkas_order.pk, {leberkas.pk: 1 + index}, user)
            save_defaults(user, {weisswurst.pk: 2, brezel.pk: 1})
            request_id = uuid.UUID(f"00000000-0000-4000-8000-{user.pk:012d}")
            if not user.ledger.filter(reference=f"manual:{user.pk}:{request_id}").exists():
                manual_payment(
                    user,
                    2000,
                    "CASH",
                    "Demo-Guthaben",
                    request_id,
                )

        today = timezone.localdate()
        historical_events = [
            (today - timedelta(days=7), EventType.WEISSWURST),
            (today - timedelta(days=14), EventType.LEBERKAESE),
        ]
        for event_date, event_type in historical_events:
            event, _ = Event.objects.get_or_create(
                date=event_date,
                event_type=event_type,
                defaults={
                    "deadline": timezone.now() - timedelta(days=1),
                    "status": Event.Status.SETTLED,
                },
            )
            if event.status != Event.Status.SETTLED:
                event.status = Event.Status.SETTLED
                event.save(update_fields=["status"])
            for index, user in enumerate(users[:2]):
                order = user.orders.get_or_create(event=event)[0]
                quantities = (
                    {weisswurst.pk: 2 - index, brezel.pk: 1}
                    if event_type == EventType.WEISSWURST
                    else {leberkas.pk: 1 + index}
                )
                save_order(order.pk, quantities, users[2])

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready. Users: anna.demo@example.org, ben.demo@example.org, "
                "admin.demo@example.org; password: demo."
            )
        )

    def call_command(self, name):
        from django.core.management import call_command

        call_command(name, stdout=self.stdout)
