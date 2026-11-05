from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import CommentReport, Review, ReviewComment, ReviewReport
from apps.moderation.serializers import CommentReportSerializer, ReviewReportSerializer


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def comment_report(request, comment_id):
    """POST /moderation/comments/<id>/report/ {reason, details?}"""
    comment = get_object_or_404(
        ReviewComment.objects.filter(
            status=ReviewComment.Status.PUBLISHED, deleted_at__isnull=True
        ),
        pk=comment_id,
    )

    if comment.user_id == request.user.id:
        return Response(
            {"error": "You can't report your own comment."},
            status=http.HTTP_400_BAD_REQUEST,
        )

    serializer = CommentReportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    _, created = CommentReport.objects.get_or_create(
        comment=comment,
        reporter=request.user,
        defaults={
            "reason": serializer.validated_data["reason"],
            "details": serializer.validated_data.get("details"),
        },
    )
    return Response(
        {"reported": True, "already_reported": not created},
        status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
    )

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def review_report(request, review_id):
    """POST /moderation/reviews/<id>/report/ {reason, details?}"""
    review = get_object_or_404(
        Review.objects.filter(
            status=Review.Status.PUBLISHED, deleted_at__isnull=True
        ),
        pk=review_id,
    )

    if review.user_id == request.user.id:
        return Response(
            {"error": "You can't report your own review."},
            status=http.HTTP_400_BAD_REQUEST,
        )

    serializer = ReviewReportSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    _, created = ReviewReport.objects.get_or_create(
        review=review,
        reporter=request.user,
        defaults={
            "reason": serializer.validated_data["reason"],
            "details": serializer.validated_data.get("details"),
        },
    )
    return Response(
        {"reported": True, "already_reported": not created},
        status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
    )