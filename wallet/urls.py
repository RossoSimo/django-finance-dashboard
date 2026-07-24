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
]
