from django.urls import path

from . import views

app_name = "newsfeed"

urlpatterns = [
    path("", views.news_home, name="home"),
]
