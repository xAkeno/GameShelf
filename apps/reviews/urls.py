from django.urls import path

from .views import (
    comment_detail,
    comment_like,
    external_reviews,
    game_rating,
    game_reviews,
    review_comments,
    review_detail,
    review_like,
)

urlpatterns = [
    # Ratings (1-10)
    path("ratings/<int:app_id>/", game_rating, name="game-rating"),
    # GameShelf reviews
    path("<int:app_id>/", game_reviews, name="game-reviews"),
    path("<int:review_id>/", review_detail, name="review-detail"),
    path("<int:review_id>/like/", review_like, name="review-like"),
    path(
        "<int:review_id>/comments/",
        review_comments,
        name="review-comments",
    ),
    # Comments
    path("comments/<int:comment_id>/", comment_detail, name="comment-detail"),
    path("comments/<int:comment_id>/like/", comment_like, name="comment-like"),
    # Third-party reviews
    path(
        "external-reviews/<int:app_id>/",
        external_reviews,
        name="external-reviews",
    ),
]