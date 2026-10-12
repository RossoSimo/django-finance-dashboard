from django.conf import settings
from django.db import models

CURRENCY_CHOICES = [
    ("EUR", "EUR - Euro"),
    ("USD", "USD - US dollar"),
    ("GBP", "GBP - British pound"),
    ("CHF", "CHF - Swiss franc"),
    ("JPY", "JPY - Japanese yen"),
    ("CAD", "CAD - Canadian dollar"),
    ("AUD", "AUD - Australian dollar"),
    ("SEK", "SEK - Swedish krona"),
    ("NOK", "NOK - Norwegian krone"),
    ("DKK", "DKK - Danish krone"),
    ("PLN", "PLN - Polish zloty"),
]


class Profile(models.Model):
    """Per-user preferences."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    base_currency = models.CharField(
        max_length=3, choices=CURRENCY_CHOICES, default=settings.DEFAULT_BASE_CURRENCY,
        help_text="Totals, net worth and charts are converted into this currency.",
    )

    def __str__(self):
        return f"Profile of {self.user}"


def get_profile(user) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def get_base_currency(user) -> str:
    return get_profile(user).base_currency
