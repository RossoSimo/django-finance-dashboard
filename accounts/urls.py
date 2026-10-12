from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

# Password reset is left out on purpose: it needs outgoing email, which
# this app doesn't configure. Admins can reset passwords from /admin/.
urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("settings/", views.profile_settings, name="settings"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("password/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
]
