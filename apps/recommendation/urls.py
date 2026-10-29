from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.include_format_suffixes = False
router.register("recommendations", views.RecommendationViewSet, basename="recommendation")
router.register("players-like-you", views.PlayersLikeYouViewSet, basename="players-like-you")
router.register("similar", views.SimilarGamesViewSet, basename="similar")
router.register("top-games", views.TopGamesViewSet, basename="top-games")
router.register("clusters", views.ClusterViewSet, basename="cluster")
router.register(
    "taste",
    views.TasteRecommendationViewSet,
    basename="taste",
)

urlpatterns = [
    path("", include(router.urls)),
]