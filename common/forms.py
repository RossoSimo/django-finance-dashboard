from django import forms


class BootstrapFormMixin:
    """Adds Bootstrap's `form-control` / `form-select` classes to every field
    automatically, so templates can just do {{ field }} without repeating
    widget attrs everywhere."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            css_class = "form-select" if isinstance(widget, forms.Select) else "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


class UserOwnedFormMixin:
    """For ModelForms whose model has a `user` FK: pass `user=request.user`.

    The instance is assigned to that user, and any ModelChoiceField whose
    model also has a `user` FK is limited to that user's objects, so a form
    can never point at another user's data.
    """

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance.user = user
        for field in self.fields.values():
            queryset = getattr(field, "queryset", None)
            if queryset is not None and hasattr(queryset.model, "user"):
                field.queryset = queryset.filter(user=user)

    def clean_ticker(self):
        """Normalise the ticker and enforce per-user uniqueness (the model
        constraint can't be checked by the form because `user` isn't a field)."""
        ticker = self.cleaned_data["ticker"].upper().strip()
        clash = self._meta.model.objects.filter(user=self.user, ticker=ticker).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f"You already have {ticker}.")
        return ticker
