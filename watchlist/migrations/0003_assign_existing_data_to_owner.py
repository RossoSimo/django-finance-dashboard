"""Give rows created before multi-user support to the oldest superuser
(or the oldest user if there is no superuser)."""

from django.conf import settings
from django.db import migrations

MODELS = ["WatchlistItem"]


def assign_owner(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    querysets = [apps.get_model("watchlist", m).objects.filter(user__isnull=True) for m in MODELS]
    if not any(qs.exists() for qs in querysets):
        return
    owner = (User.objects.filter(is_superuser=True).order_by("pk").first()
             or User.objects.order_by("pk").first())
    if owner is None:
        raise RuntimeError(
            "Existing watchlist data needs an owner. Run `python manage.py createsuperuser`, "
            "then `python manage.py migrate` again."
        )
    for qs in querysets:
        qs.update(user=owner)


class Migration(migrations.Migration):

    dependencies = [
        ("watchlist", "0002_add_user"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(assign_owner, migrations.RunPython.noop),
    ]
