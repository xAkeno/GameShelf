"""
Django settings for gameshelf project.
"""

import os
from pathlib import Path

# ─── 1. CORE PATHS ───────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent


# ─── 2. SECURITY & ENVIRONMENT SETTINGS ──────────────────────────────────────
# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", 
    "django-insecure-frr+2!u1v+eqsvihwf(-au&oioct8v7*jziwdj#(b6v=h*#m6l"
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DEBUG", "True") == "True"

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",") if os.environ.get("ALLOWED_HOSTS") else []


# ─── 3. APPLICATION DEFINITION ───────────────────────────────────────────────
INSTALLED_APPS = [
    # Django Contrib Apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-Party Apps
    "rest_framework",
    "corsheaders",
    
    # GameShelf Local Apps
    "apps.core",
    "apps.user",
    "apps.library",
    "apps.games",
    "apps.reviews",
    "apps.recommendation",
    "apps.analytics",
    "apps.moderation",
    "apps.social",
    'django_extensions',
]


# ─── 4. MIDDLEWARE ───────────────────────────────────────────────────────────
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ─── 5. URL & WSGI CONFIGURATION ─────────────────────────────────────────────
ROOT_URLCONF = 'gameshelf.urls'
WSGI_APPLICATION = 'gameshelf.wsgi.application'


# ─── 6. TEMPLATES ────────────────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ─── 7. DATABASE ─────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# ─── 8. AUTHENTICATION & PASSWORD VALIDATION ──────────────────────────────────
AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# ─── 9. INTERNATIONALIZATION & TIME ──────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ─── 10. STATIC & MEDIA FILES ────────────────────────────────────────────────
STATIC_URL = 'static/'

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ─── 11. CORS & CSRF CONFIGURATION ───────────────────────────────────────────
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
]

CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
]


# ─── 12. REST FRAMEWORK CONFIGURATION ─────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "register": "5/hour",
        "login": "10/minute",
        "verification": "5/hour",
        "password_forgot": "5/hour",
        "password_reset": "20/hour",
        "friend_request": "20/hour",
    },
}


# ─── 13. EMAIL & FRONTEND URLS ────────────────────────────────────────────────
FRONTEND_URL = "http://localhost:5173"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "noreply@gameshelf.local"


# ─── 14. THIRD-PARTY API KEYS ────────────────────────────────────────────────
STEAM_API_KEY = os.environ.get("STEAM_API_KEY", "5D48239956EB027F5F31D265CD43BD9A")