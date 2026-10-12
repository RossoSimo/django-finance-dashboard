from django import forms
from django.contrib.auth.forms import UserCreationForm

from common.forms import BootstrapFormMixin

from .models import Profile


class SignupForm(BootstrapFormMixin, UserCreationForm):
    pass


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["base_currency"]
