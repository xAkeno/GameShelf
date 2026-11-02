import uuid

import requests
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    OuterRef,
    Q,
    Subquery,
    Value,
)
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from apps.reviews.webscraper import SUPPORTED_SOURCES, fetch_external_reviews
from apps.library.views import _get_or_create_game
from apps.core.models import (
    CommentAttachment,
    CommentLike,
    Game,
    Rating,
    Review,
    ReviewComment,
    ReviewLike,
    GameLike,
)
from apps.reviews.serializers import (
    CommentCreateSerializer,
    CommentEditSerializer,
    CommentSerializer,
    RatingWriteSerializer,
    ReviewSerializer,
    ReviewWriteSerializer,
)

try:  # Pillow is used to verify uploads and read their size. Optional.
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}
ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 30


def _to_int(value, default, minimum=1):
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


def _flag(model, request, fk_name):
    """Annotation: did the current user like this row? (False if anonymous)"""
    if not request.user.is_authenticated:
        return Value(False, output_field=BooleanField())
    return Exists(model.objects.filter(user=request.user, **{fk_name: OuterRef("pk")}))


def _resolve_game(app_id, create):
    """
    Returns (game, error_response).
    """
    try:
        game = _get_or_create_game(app_id)
    except requests.RequestException:
        if not create:
            return None, None

        return None, Response(
            {"error": "Unable to fetch game details from Steam."},
            status=http.HTTP_502_BAD_GATEWAY,
        )

    if game is None:
        if not create:
            return None, None

        return None, Response(
            {"error": "Game not found on Steam."},
            status=http.HTTP_404_NOT_FOUND,
        )

    return game, None


def _live(model_qs):
    return model_qs.filter(deleted_at__isnull=True)


# ---------------------------------------------------------------------------
# Querysets (annotated for the serializers)
# ---------------------------------------------------------------------------
def _reviews_qs(request):
    live_comments = Q(
        comments__status=ReviewComment.Status.PUBLISHED,
        comments__deleted_at__isnull=True,
    )
    return (
        _live(Review.objects.filter(status=Review.Status.PUBLISHED))
        .select_related("user")
        .annotate(
            like_count=Count("likes", distinct=True),
            comment_count=Count("comments", filter=live_comments, distinct=True),
            liked_by_me=_flag(ReviewLike, request, "review"),
            author_score=Subquery(
                Rating.objects.filter(
                    user=OuterRef("user"), game=OuterRef("game")
                ).values("score")[:1]
            ),
        )
    )


def _comments_qs(request):
    return (
        _live(ReviewComment.objects.filter(status=ReviewComment.Status.PUBLISHED))
        .select_related("user")
        .prefetch_related("attachments")
        .annotate(
            like_count=Count("likes", distinct=True),
            liked_by_me=_flag(CommentLike, request, "comment"),
        )
    )


def _get_review_or_404(review_id):
    return get_object_or_404(
        _live(Review.objects.filter(status=Review.Status.PUBLISHED)), pk=review_id
    )


def _get_comment_or_404(comment_id):
    return get_object_or_404(
        _live(ReviewComment.objects.filter(status=ReviewComment.Status.PUBLISHED)),
        pk=comment_id,
    )


def _forbidden():
    return Response(
        {"error": "You can only change your own content."},
        status=http.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# Ratings  (1-10, one per user per game)
# ---------------------------------------------------------------------------
def _rating_stats(game, user):
    if game is None:
        return {"my_score": None, "average": None, "count": 0}

    aggregate = Rating.objects.filter(game=game).aggregate(
        average=Avg("score"), count=Count("id")
    )
    mine = None
    if user.is_authenticated:
        mine = (
            Rating.objects.filter(game=game, user=user)
            .values_list("score", flat=True)
            .first()
        )

    average = aggregate["average"]
    return {
        "my_score": mine,
        "average": round(float(average), 1) if average is not None else None,
        "count": aggregate["count"],
    }


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def game_rating(request, app_id):
    """
    GET    /ratings/steam/<app_id>/          -> { my_score, average, count }
    PUT    /ratings/steam/<app_id>/ {score}  -> set / change my rating
    DELETE /ratings/steam/<app_id>/          -> remove my rating
    """
    if request.method == "GET":
        game, _ = _resolve_game(app_id, create=False)
        return Response(_rating_stats(game, request.user))

    game, error = _resolve_game(app_id, create=request.method == "PUT")
    if error:
        return error

    if request.method == "PUT":
        serializer = RatingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Rating.objects.update_or_create(
            user=request.user,
            game=game,
            defaults={"score": serializer.validated_data["score"]},
        )
    elif game is not None:
        Rating.objects.filter(user=request.user, game=game).delete()

    return Response(_rating_stats(game, request.user))


# ---------------------------------------------------------------------------
# Game likes  (one per user per game)
# ---------------------------------------------------------------------------
def _like_stats(game, user):
    if game is None:
        return {"liked": False, "like_count": 0}

    return {
        "liked": (
            user.is_authenticated
            and GameLike.objects.filter(game=game, user=user).exists()
        ),
        "like_count": GameLike.objects.filter(game=game).count(),
    }


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def game_like(request, app_id):
    """
    GET    /reviews/likes/<app_id>/ -> { liked, like_count }
    PUT    /reviews/likes/<app_id>/ -> like the game
    DELETE /reviews/likes/<app_id>/ -> remove my like
    """
    if request.method == "GET":
        game, _ = _resolve_game(app_id, create=False)
        return Response(_like_stats(game, request.user))

    game, error = _resolve_game(app_id, create=request.method == "PUT")
    if error:
        return error

    if request.method == "PUT":
        GameLike.objects.get_or_create(user=request.user, game=game)
    elif game is not None:
        GameLike.objects.filter(user=request.user, game=game).delete()

    return Response(_like_stats(game, request.user))

# ---------------------------------------------------------------------------
# GameShelf reviews
# ---------------------------------------------------------------------------
@api_view(["GET", "POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def game_reviews(request, app_id):
    """
    GET  /reviews/steam/<app_id>/?sort=helpful|recent&page=1&page_size=10
    POST /reviews/steam/<app_id>/ {title?, content, contains_spoilers?}
    """
    if request.method == "POST":
        return _create_review(request, app_id)

    game, _ = _resolve_game(app_id, create=False)
    if game is None:
        return Response(
            {
                "count": 0,
                "page": 1,
                "has_next": False,
                "results": [],
                "my_review": None,
            }
        )

    queryset = _reviews_qs(request).filter(game=game)

    # Optional score filter, e.g. ?min_score=9&max_score=10 (unrated reviews are left out)
    min_score = _to_int(request.query_params.get("min_score"), None)
    max_score = _to_int(request.query_params.get("max_score"), None)
    if min_score is not None:
        queryset = queryset.filter(author_score__gte=min_score)
    if max_score is not None:
        queryset = queryset.filter(author_score__lte=max_score)

    if request.query_params.get("sort") == "recent":
        queryset = queryset.order_by("-created_at", "-id")
    else:
        queryset = queryset.order_by("-like_count", "-created_at", "-id")

    page = _to_int(request.query_params.get("page"), 1)
    page_size = min(
        _to_int(request.query_params.get("page_size"), DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE
    )
    total = queryset.count()
    start = (page - 1) * page_size
    results = queryset[start : start + page_size]

    my_review = None
    if request.user.is_authenticated:
        mine = _reviews_qs(request).filter(game=game, user=request.user).first()
        if mine:
            my_review = ReviewSerializer(mine, context={"request": request}).data

    return Response(
        {
            "count": total,
            "page": page,
            "has_next": start + page_size < total,
            "results": ReviewSerializer(
                results, many=True, context={"request": request}
            ).data,
            "my_review": my_review,
        }
    )


def _create_review(request, app_id):
    serializer = ReviewWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    game, error = _resolve_game(app_id, create=True)
    if error:
        return error

    if _live(Review.objects.filter(user=request.user, game=game)).exists():
        return Response(
            {"error": "You've already reviewed this game. Edit your review instead."},
            status=http.HTTP_400_BAD_REQUEST,
        )

    review = Review.objects.create(
        user=request.user, game=game, **serializer.validated_data
    )
    review = _reviews_qs(request).get(pk=review.pk)
    return Response(
        ReviewSerializer(review, context={"request": request}).data,
        status=http.HTTP_201_CREATED,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def review_detail(request, review_id):
    """PATCH /reviews/<id>/ (owner)  |  DELETE /reviews/<id>/ (owner, soft delete)"""
    review = _get_review_or_404(review_id)
    if review.user_id != request.user.id:
        return _forbidden()

    if request.method == "DELETE":
        review.deleted_at = timezone.now()
        review.save(update_fields=["deleted_at"])
        return Response(status=http.HTTP_204_NO_CONTENT)

    serializer = ReviewWriteSerializer(review, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    changed = any(
        field in serializer.validated_data
        and serializer.validated_data[field] != getattr(review, field)
        for field in ("title", "content", "contains_spoilers")
    )
    if changed:
        serializer.save(edited_at=timezone.now())

    review = _reviews_qs(request).get(pk=review.pk)
    return Response(ReviewSerializer(review, context={"request": request}).data)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def review_like(request, review_id):
    """POST /reviews/<id>/like/ = like  |  DELETE = unlike"""
    review = _get_review_or_404(review_id)

    if request.method == "POST":
        ReviewLike.objects.get_or_create(user=request.user, review=review)
    else:
        ReviewLike.objects.filter(user=request.user, review=review).delete()

    return Response(
        {
            "liked": request.method == "POST",
            "like_count": ReviewLike.objects.filter(review=review).count(),
        }
    )


# ---------------------------------------------------------------------------
# Comments (+ image attachments)
# ---------------------------------------------------------------------------
def _prepare_attachments(files):
    """Validate uploads. Returns a list of dicts ready to be stored."""
    if len(files) > MAX_ATTACHMENTS:
        raise ValidationError(
            {"attachments": [f"You can attach up to {MAX_ATTACHMENTS} images."]}
        )

    prepared = []
    for upload in files:
        if upload.size > MAX_ATTACHMENT_BYTES:
            raise ValidationError(
                {"attachments": ["Each image must be 5 MB or smaller."]}
            )
        if upload.content_type not in ALLOWED_IMAGE_TYPES:
            raise ValidationError(
                {"attachments": ["Only JPG, PNG, WebP and GIF images are allowed."]}
            )

        extension = ALLOWED_IMAGE_TYPES[upload.content_type]
        width = height = None

        if Image is not None:
            try:
                Image.open(upload).verify()
                upload.seek(0)
                image = Image.open(upload)
                width, height = image.size
                image_format = image.format
                upload.seek(0)
            except Exception:
                raise ValidationError(
                    {"attachments": ["One of the files isn't a valid image."]}
                )
            if image_format not in ALLOWED_PIL_FORMATS:
                raise ValidationError(
                    {"attachments": ["Only JPG, PNG, WebP and GIF images are allowed."]}
                )
            extension = {"JPEG": "jpg"}.get(image_format, image_format.lower())

        prepared.append(
            {
                "file": upload,
                "extension": extension,
                "media_type": (
                    CommentAttachment.MediaType.GIF
                    if extension == "gif"
                    else CommentAttachment.MediaType.IMAGE
                ),
                "width": width,
                "height": height,
            }
        )
    return prepared


def _store_attachment(comment, item):
    # Uploaded files only: the URL always points at our own storage.
    path = default_storage.save(
        f"comment_attachments/{uuid.uuid4().hex}.{item['extension']}", item["file"]
    )
    return CommentAttachment.objects.create(
        comment=comment,
        media_type=item["media_type"],
        file_url=default_storage.url(path),
        file_size=item["file"].size,
        width=item["width"],
        height=item["height"],
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def review_comments(request, review_id):
    """
    GET  /reviews/<id>/comments/  -> flat list (oldest first); replies carry
                                     `parent_comment`
    POST /reviews/<id>/comments/  multipart: content, parent_comment?, attachments[]
    """
    review = _get_review_or_404(review_id)

    if request.method == "GET":
        comments = (
            _comments_qs(request)
            .filter(review=review)
            .filter(
                Q(parent_comment__isnull=True)
                | Q(
                    parent_comment__deleted_at__isnull=True,
                    parent_comment__status=ReviewComment.Status.PUBLISHED,
                )
            )
            .order_by("created_at", "id")
        )
        return Response(
            CommentSerializer(comments, many=True, context={"request": request}).data
        )

    serializer = CommentCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    content = serializer.validated_data["content"]

    files = request.FILES.getlist("attachments")
    if not content and not files:
        raise ValidationError({"content": ["Write something or attach an image."]})
    prepared = _prepare_attachments(files)

    parent = None
    parent_id = serializer.validated_data.get("parent_comment")
    if parent_id:
        parent = _live(
            ReviewComment.objects.filter(
                pk=parent_id, review=review, status=ReviewComment.Status.PUBLISHED
            )
        ).first()
        if parent is None:
            raise ValidationError({"parent_comment": ["That comment no longer exists."]})
        # Keep threads one level deep: replying to a reply replies to its parent.
        if parent.parent_comment_id:
            parent = parent.parent_comment

    with transaction.atomic():
        comment = ReviewComment.objects.create(
            review=review, user=request.user, parent_comment=parent, content=content
        )
        for item in prepared:
            _store_attachment(comment, item)

    comment = _comments_qs(request).get(pk=comment.pk)
    return Response(
        CommentSerializer(comment, context={"request": request}).data,
        status=http.HTTP_201_CREATED,
    )


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def comment_detail(request, comment_id):
    """PATCH /reviews/comments/<id>/ {content} (owner)  |  DELETE (owner, soft delete)"""
    comment = _get_comment_or_404(comment_id)
    if comment.user_id != request.user.id:
        return _forbidden()

    if request.method == "DELETE":
        comment.deleted_at = timezone.now()
        comment.save(update_fields=["deleted_at"])
        return Response(status=http.HTTP_204_NO_CONTENT)

    serializer = CommentEditSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    new_content = serializer.validated_data["content"]

    if new_content != comment.content:
        comment.content = new_content
        comment.edited_at = timezone.now()
        comment.save(update_fields=["content", "edited_at"])

    comment = _comments_qs(request).get(pk=comment.pk)
    return Response(CommentSerializer(comment, context={"request": request}).data)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticatedOrReadOnly])
def comment_like(request, comment_id):
    """POST /comments/<id>/like/ = like  |  DELETE = unlike"""
    comment = _get_comment_or_404(comment_id)

    if request.method == "POST":
        CommentLike.objects.get_or_create(user=request.user, comment=comment)
    else:
        CommentLike.objects.filter(user=request.user, comment=comment).delete()

    return Response(
        {
            "liked": request.method == "POST",
            "like_count": CommentLike.objects.filter(comment=comment).count(),
        }
    )


# ---------------------------------------------------------------------------
# Third-party reviews (Steam, Reddit, Metacritic)
# ---------------------------------------------------------------------------
@api_view(["GET"])
def external_reviews(request, app_id):
    """GET /external-reviews/steam/<app_id>/?source=steam|reddit|metacritic&cursor=*"""
    source = request.query_params.get("source", "steam")
    if source not in SUPPORTED_SOURCES:
        return Response(
            {"error": f"Unsupported source. Choose one of: {', '.join(SUPPORTED_SOURCES)}."},
            status=http.HTTP_400_BAD_REQUEST,
        )

    cursor = request.query_params.get("cursor") or "*"
    limit = min(_to_int(request.query_params.get("limit"), 5), 20)

    try:
        return Response(fetch_external_reviews(source, app_id, cursor, limit))
    except requests.RequestException:
        return Response(
            {"error": f"{source.title()} isn't responding right now."},
            status=http.HTTP_502_BAD_GATEWAY,
        )