from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.http import Http404
from django.shortcuts import redirect, render

from .forms import SignupForm


@login_not_required
def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404("Sign-up is disabled.")
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Welcome, {user.username}!")
            return redirect(settings.LOGIN_REDIRECT_URL)
    else:
        form = SignupForm()
    return render(request, "registration/signup.html", {"form": form})
