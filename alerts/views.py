from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import PriceAlertForm
from .models import PriceAlert
from .services import check_alerts, dismiss, rearm


def _back(request, default="alerts:home"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return redirect(target)
    return redirect(default)


def alerts_home(request):
    check_alerts(request.user)
    alerts = PriceAlert.objects.filter(user=request.user)
    return render(request, "alerts/alerts_home.html", {
        "active": alerts.filter(is_active=True).order_by("ticker"),
        "triggered": alerts.filter(is_active=False).order_by("-triggered_at"),
    })


def add_alert(request):
    if request.method == "POST":
        form = PriceAlertForm(request.POST, user=request.user)
        if form.is_valid():
            alert = form.save()
            messages.success(request, f"Alert set: {alert}.")
            return redirect("alerts:home")
    else:
        form = PriceAlertForm(user=request.user, initial={"ticker": request.GET.get("ticker", "").upper()})
    return render(request, "wallet/form.html", {"form": form, "title": "New price alert"})


@require_POST
def dismiss_alert(request, pk):
    dismiss(PriceAlert.objects.filter(user=request.user, pk=pk))
    return _back(request)


@require_POST
def dismiss_all(request):
    dismiss(PriceAlert.objects.filter(user=request.user))
    return _back(request)


@require_POST
def rearm_alert(request, pk):
    alert = rearm(get_object_or_404(PriceAlert, pk=pk, user=request.user))
    messages.success(request, f"Re-armed: {alert}.")
    return redirect("alerts:home")


@require_POST
def delete_alert(request, pk):
    alert = get_object_or_404(PriceAlert, pk=pk, user=request.user)
    alert.delete()
    messages.success(request, f"Deleted alert: {alert}.")
    return redirect("alerts:home")
