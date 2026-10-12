from django.contrib.auth.forms import UserCreationForm

from common.forms import BootstrapFormMixin


class SignupForm(BootstrapFormMixin, UserCreationForm):
    pass
