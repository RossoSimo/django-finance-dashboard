from django.urls import path

from . import views

app_name = "watchlist"

urlpatterns = [
    path("", views.watchlist_home, name="home"),
    path("add/", views.add_watchlist_item, name="add"),
    path("<int:pk>/remove/", views.remove_watchlist_item, name="remove"),
]
