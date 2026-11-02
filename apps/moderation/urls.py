from django.urls import path
from . import views

urlpatterns = [
    path("reviews/<int:review_id>/report/", views.review_report),
    path("comments/<int:comment_id>/report/", views.comment_report),
]