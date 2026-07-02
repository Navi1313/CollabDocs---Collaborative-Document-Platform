from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='api.Document')
def create_document_audit_log(sender, instance, created, **kwargs):
    """
    Fires automatically every time a Document is saved.
    'created' = True on INSERT, False on UPDATE.
    We never call this from views — Django fires it via the signal system.
    """
    from api.models import AuditLog  # local import avoids circular import

    AuditLog.objects.create(
        actor=instance.created_by,
        action='created' if created else 'updated',
        model_name='Document',
        object_id=str(instance.id),  # convert UUID → string, AuditLog.object_id is CharField(100)
    )