from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from apps.core.models import SecurityEvent

from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserMeSerializer,
    UserProfileSerializer,
    VerifyEmailSerializer,
)

User = get_user_model()


# ─── Email verification and security log ──────────────────────────────────────
class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """One-time token: it stops working once email_verified_at is set,
    or if the user's email changes."""

    key_salt = "users.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.email_verified_at}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()


def send_verification_email(user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    link = f"{settings.FRONTEND_URL}/verify-email?uid={uid}&token={token}"
    send_mail(
        subject="Verify your GameShelf email",
        message=(
            f"Hi {user.username},\n\n"
            f"Confirm your email address to finish setting up your account:\n{link}\n\n"
            "If you didn't create a GameShelf account, you can ignore this email."
        ),
        from_email=None,  # uses DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )


def confirm_email(uid: str, token: str):
    """Returns the user if the link is valid, otherwise None."""
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    if not email_verification_token.check_token(user, token):
        return None
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at", "updated_at"])
    return user


def log_failed_login(request, username) -> None:
    """Security log for failed logins. Stores the user (if the name exists) and the IP,
    never the password or the text that was typed."""
    user = User.objects.filter(username__iexact=str(username or "")[:150]).first()
    SecurityEvent.objects.create(
        event_type=SecurityEvent.EventType.LOGIN_FAILED,
        user=user,
        ip_address=request.META.get("REMOTE_ADDR") or None,
        path=request.path[:255],
    )


# ─── Views ────────────────────────────────────────────────────────────────────
class AuthViewSet(viewsets.ViewSet):
    """/auth/csrf, register, login, logout, verify-email, resend-verification, password/change"""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    # action name -> rate in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    throttle_scopes = {
        "register": "register",
        "login": "login",
        "verify_email": "verification",
        "resend_verification": "verification",
    }

    def get_throttles(self):
        self.throttle_scope = self.throttle_scopes.get(self.action)
        return super().get_throttles()

    @action(detail=False, methods=["get"], authentication_classes=[])
    def csrf(self, request):
        """Call once when the React app starts so the browser gets the csrftoken cookie."""
        get_token(request)
        return Response({"detail": "CSRF cookie set."})

    @action(detail=False, methods=["post"])
    def register(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        send_verification_email(user)
        return Response(
            {"detail": "Account created. Check your email to verify your address."},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"])
    def login(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            log_failed_login(request, request.data.get("username"))
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        if user.is_suspended:
            return Response(
                {"detail": f"Your account is suspended until {user.suspended_until:%Y-%m-%d}."},
                status=status.HTTP_403_FORBIDDEN,
            )
        auth_login(request, user)  # creates the session cookie
        return Response(UserMeSerializer(user).data)

    @action(detail=False, methods=["post"], permission_classes=[permissions.IsAuthenticated])
    def logout(self, request):
        auth_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="verify-email")
    def verify_email(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if confirm_email(**serializer.validated_data) is None:
            return Response(
                {"detail": "This verification link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"detail": "Email verified."})

    @action(
        detail=False,
        methods=["post"],
        url_path="resend-verification",
        permission_classes=[permissions.IsAuthenticated],
    )
    def resend_verification(self, request):
        if request.user.email_verified_at:
            return Response({"detail": "Your email is already verified."})
        send_verification_email(request.user)
        return Response({"detail": "Verification email sent."})

    @action(
        detail=False,
        methods=["post"],
        url_path="password/change",
        permission_classes=[permissions.IsAuthenticated],
    )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password", "updated_at"])
        update_session_auth_hash(request, request.user)  # keeps this session logged in
        return Response({"detail": "Password changed."})


class UserViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """/users/<username>/ is the public profile, /users/me/ is the logged-in user."""

    queryset = User.objects.filter(is_active=True)
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "username"
    lookup_value_regex = r"[\w.@+-]+"  # usernames may contain . @ + -

    @action(
        detail=False,
        methods=["get", "patch"],
        permission_classes=[permissions.IsAuthenticated],
        serializer_class=UserMeSerializer,
    )
    def me(self, request):
        """GET the current user, PATCH profile and privacy fields."""
        if request.method == "PATCH":
            serializer = self.get_serializer(request.user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        return Response(self.get_serializer(request.user).data)
