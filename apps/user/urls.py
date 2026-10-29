from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.include_format_suffixes = False  
router.register("auth", views.AuthViewSet, basename="auth")
router.register("users", views.UserViewSet, basename="user")
router.register(r"users/friend-requests", views.FriendshipViewSet, basename="user-friend-requests")
router.register(r"blocks", views.BlockViewSet, basename="blocks")
router.register(r"reports", views.ReportViewSet, basename="reports")  

urlpatterns = [
    path("users/import-steam/", views.ImportSteamLibraryView.as_view(), name="import-steam"),
    path("", include(router.urls)),
]