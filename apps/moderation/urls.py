from django.urls import path
from . import views

urlpatterns = [
    # ...your existing routes
    path("comments/<int:comment_id>/report/", views.comment_report),
]