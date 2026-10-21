from rest_framework import serializers

from apps.core.models import CommentReport


class CommentReportSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=CommentReport.Reason.choices)
    details = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=500
    )

    def validate_details(self, value):
        # The model column is nullable, so store None instead of an empty string.
        return (value or "").strip() or None