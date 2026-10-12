from django.conf import settings


def signup_enabled(request):
    return {"signup_enabled": settings.SIGNUP_ENABLED}
