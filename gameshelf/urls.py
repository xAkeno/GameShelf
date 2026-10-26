"""
URL configuration for gameshelf project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

"""
URL configuration for gameshelf project.
"""
from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [

    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.user.urls")),
    path("api/v1/games/", include("apps.games.urls")),
    path("api/v1/list/", include("apps.library.urls")),
    path("api/v1/reviews/", include("apps.reviews.urls")),
    path("api/v1/moderation/", include("apps.moderation.urls")),
    path("api/v1/social/", include("apps.social.urls")),

    # Recommendation endpoints
    path("api/v1/", include("apps.recommendation.urls")),
]


if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )