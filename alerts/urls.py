from django.urls import path

from . import views

app_name = "alerts"

urlpatterns = [
    path("", views.alerts_home, name="home"),
    path("add/", views.add_alert, name="add"),
    path("dismiss/", views.dismiss_all, name="dismiss_all"),
    path("<int:pk>/dismiss/", views.dismiss_alert, name="dismiss"),
    path("<int:pk>/rearm/", views.rearm_alert, name="rearm"),
    path("<int:pk>/delete/", views.delete_alert, name="delete"),
]
