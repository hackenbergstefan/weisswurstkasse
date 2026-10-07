import logging
from pprint import pformat

logger = logging.getLogger("weisswurstrunde.audit")


def record(action, actor=None, **details):
    actor_label = getattr(actor, "email", None) or f"id={getattr(actor, 'pk', 'system')}"
    logger.info(
        "%s | actor=%s | %s", action, actor_label, pformat(details, sort_dicts=True, width=160)
    )
