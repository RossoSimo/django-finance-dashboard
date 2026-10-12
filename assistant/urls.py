from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("chat/", views.chat, name="chat"),
    path("chat/clear/", views.clear_chat, name="clear_chat"),
    path("import/", views.import_upload, name="import_upload"),
    path("import/preview/", views.import_preview, name="import_preview"),
]
