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
