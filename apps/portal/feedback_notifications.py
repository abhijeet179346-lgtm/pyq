"""Send a notification for a saved student feedback item."""
import logging

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.utils import timezone

logger = logging.getLogger(__name__)


def notify_feedback(feedback):
    recipients = settings.FEEDBACK_NOTIFICATION_RECIPIENTS
    if not recipients:
        logger.warning("Feedback notification has no recipients configured; feedback %s remains pending", feedback.pk)
        return False
    lines = [
        f"Type: {feedback.get_category_display()}",
        f"Institute: {feedback.tenant.name} ({feedback.tenant.slug})",
        f"Student: {feedback.reporter.username} ({feedback.reporter.email})",
        f"Page: {feedback.page_path or 'Not provided'}",
        f"Question ID: {feedback.question_id or 'Not provided'}",
        "", "Message:", feedback.message,
    ]
    try:
        connection = get_connection(backend=settings.FEEDBACK_EMAIL_BACKEND or settings.EMAIL_BACKEND)
        email = EmailMessage(subject=f"PYQNet {feedback.get_category_display()} #{feedback.pk}",
                             body="\n".join(lines), from_email=settings.FEEDBACK_FROM_EMAIL,
                             to=recipients, connection=connection)
        if email.send(fail_silently=False) != 1:
            raise RuntimeError("Email backend did not accept the notification")
    except Exception as exc:
        logger.warning("Feedback notification %s failed (%s)", feedback.pk, type(exc).__name__)
        return False
    feedback.notification_sent_at = timezone.now()
    feedback.save(update_fields=["notification_sent_at"])
    return True
