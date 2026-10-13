from django.urls import path

from . import views

app_name = "performance"

urlpatterns = [
    path("", views.portfolio, name="portfolio"),
    path("chart-data/", views.chart_data, name="chart_data"),
    path("holding/<int:pk>/", views.holding_detail, name="holding"),
]
