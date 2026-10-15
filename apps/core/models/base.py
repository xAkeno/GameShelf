from django.db import models
from django.db.models import Q


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Rows are hidden by setting deleted_at, never removed (keeps the audit trail)."""

    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_deleted(self):
        return self.deleted_at is not None


def exactly_one(*fields):
    """Q object that is true when exactly one of the given nullable fields is set.
    Use inside a CheckConstraint."""
    terms = []
    for chosen in fields:
        term = Q(**{f"{chosen}__isnull": False})
        for other in fields:
            if other != chosen:
                term &= Q(**{f"{other}__isnull": True})
        terms.append(term)
    combined = terms[0]
    for term in terms[1:]:
        combined |= term
    return combined
