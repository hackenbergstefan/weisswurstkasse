import logging
import threading
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from weisswurstrunde.models import LoginAttempt
from weisswurstrunde.paypal import mailbox_enabled

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Generate events and reconcile PayPal mail every 15 minutes. Run exactly one worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                call_command("generate_events")
                call_command("reconcile", paypal=mailbox_enabled())
                LoginAttempt.objects.filter(
                    window_start__lt=timezone.now() - timedelta(days=1)
                ).delete()
            except Exception:
                logger.exception("Scheduled maintenance failed; will retry on next cycle.")
                if options["once"]:
                    raise
            if options["once"]:
                return
            threading.Event().wait(900)
