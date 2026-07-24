from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("dashboard.urls")),
    path("wallet/", include("wallet.urls")),
    path("watchlist/", include("watchlist.urls")),
    path("news/", include("newsfeed.urls")),
]
