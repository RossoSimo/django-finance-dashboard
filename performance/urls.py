from django.urls import path

from . import views

app_name = "performance"

urlpatterns = [
    path("", views.portfolio, name="portfolio"),
    path("holding/<int:pk>/", views.holding_detail, name="holding"),
]
