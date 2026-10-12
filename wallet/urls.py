from django.urls import path

from . import views

app_name = "wallet"

urlpatterns = [
    path("", views.wallet_home, name="home"),
    path("cash/add/", views.add_cash_account, name="add_cash_account"),
    path("holdings/add/", views.add_holding, name="add_holding"),
    path("holdings/<int:pk>/delete/", views.delete_holding, name="delete_holding"),
    path("transactions/", views.transaction_list, name="transaction_list"),
    path("transactions/add/", views.add_transaction, name="add_transaction"),
    path("transactions/import/", views.csv_upload, name="csv_upload"),
    path("transactions/import/preview/", views.csv_preview, name="csv_preview"),
    path("transactions/import/sample.csv", views.csv_sample, name="csv_sample"),
    path("transactions/<int:pk>/edit/", views.edit_transaction, name="edit_transaction"),
    path("transactions/<int:pk>/delete/", views.remove_transaction, name="delete_transaction"),
]
