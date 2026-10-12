"""
Django settings for financedash project.
A local-first personal finance dashboard: wallet/liquidity tracking,
a watchlist for stocks/ETFs/bonds, and a news feed.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv()

# SECURITY WARNING: keep the secret key used in production secret!
# This is fine for local/dev use. Change it before deploying publicly.
SECRET_KEY = os.getenv('SECRET_KEY')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG') == 'True'

def env_list(name, default):
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


# Comma-separated, e.g. ALLOWED_HOSTS=localhost,127.0.0.1,finance.example.com
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Local apps
    "accounts",
    "assistant",
    "dashboard",
    "wallet",
    "watchlist",
    "newsfeed",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Every view requires login unless decorated with @login_not_required.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "financedash.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.signup_enabled",
            ],
        },
    },
]

WSGI_APPLICATION = "financedash.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication ---
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "login"
# Set SIGNUP_ENABLED=False to stop new self-registrations (admins can still
# create users from /admin/).
SIGNUP_ENABLED = os.getenv("SIGNUP_ENABLED", "True") == "True"

# --- App-specific settings ---

# How long (seconds) to cache live price/news lookups from yfinance,
# so the dashboard doesn't hammer Yahoo Finance on every page load.
MARKET_DATA_CACHE_SECONDS = 60
NEWS_CACHE_SECONDS = 300

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# --- Ollama (local LLM) settings ---
# Requires Ollama (https://ollama.com) running locally, with the relevant
# models pulled: `ollama pull llama3.1` and `ollama pull llava`.
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_CHAT_MODEL = "llama3.1"       # used for portfolio Q&A / analytics
OLLAMA_VISION_MODEL = "llava"        # used for screenshot -> data extraction

