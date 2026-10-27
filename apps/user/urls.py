from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.include_format_suffixes = False  # so usernames containing "." aren't read as ".json"
router.register("auth", views.AuthViewSet, basename="auth")
router.register("users", views.UserViewSet, basename="user")

urlpatterns = [
    path("users/import-steam/", views.ImportSteamLibraryView.as_view(), name="import-steam"),
    path("", include(router.urls)),
]