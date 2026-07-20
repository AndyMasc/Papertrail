import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from records.models import Record

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Record)
def auto_match_on_record_save(sender, instance, created, **kwargs):  # noqa: ARG001
    if created:
        return
    if not instance.is_active:
        return
    if getattr(instance, "_skip_auto_match", False):
        return

    transaction.on_commit(lambda: _run_auto_match(instance))


def _run_auto_match(instance: Record) -> None:
    from records.matching import try_match_document_record, try_match_plaid_record

    if instance.plaid_transaction_id:
        matched = try_match_plaid_record(instance)
        if matched:
            logger.info(
                "Auto-matched %d document(s) to updated plaid record %s",
                len(matched),
                instance.pk,
            )
    else:
        result = try_match_document_record(instance)
        if result:
            logger.info(
                "Auto-matched updated document record %s to plaid record %s",
                instance.pk,
                result.pk,
            )
