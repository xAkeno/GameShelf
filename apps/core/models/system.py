from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


class AdminAuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create"
        UPDATE = "update"
        DELETE = "delete"
        MERGE = "merge"
        RESYNC = "resync"

    admin = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="audit_log")
    entity_type = models.CharField(max_length=50)  # games, users, settings...
    entity_id = models.BigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    changes = models.JSONField(default=dict, blank=True)  # before / after values
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_audit_log"
        ordering = ["-created_at"]


class DailySiteStat(models.Model):
    """Filled by a nightly job; the admin dashboard charts it."""

    date = models.DateField(primary_key=True)
    visits = models.PositiveIntegerField(default=0)
    unique_visitors = models.PositiveIntegerField(default=0)
    new_users = models.PositiveIntegerField(default=0)
    reviews_created = models.PositiveIntegerField(default=0)
    comments_created = models.PositiveIntegerField(default=0)
    reports_created = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "daily_site_stats"
        ordering = ["-date"]


class SecurityEvent(models.Model):
    """Security log. Never store passwords, tokens or request bodies here."""

    class EventType(models.TextChoices):
        LOGIN_FAILED = "login_failed"
        PERMISSION_DENIED = "permission_denied"
        RATE_LIMITED = "rate_limited"
        RATING_SPIKE = "rating_spike"
        REVIEW_SPIKE = "review_spike"

    event_type = models.CharField(max_length=30, choices=EventType.choices)
    user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="security_events"
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=255, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "security_events"
        indexes = [models.Index(fields=["event_type", "created_at"], name="security_type_created_idx")]


class SiteSetting(models.Model):
    """Registration open/closed, upload limits, banned words, auto-hide report threshold..."""

    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "site_settings"
