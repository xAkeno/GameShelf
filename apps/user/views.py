from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from django.db.models import Max
from apps.core.models import UserFavoriteGame
from apps.core.models.users import UserAchievement
from .serializers import UserAchievementSerializer, UserFavoriteGameSerializer, AddFavoriteSerializer
from apps.library.views import _get_or_create_game
import requests
from rest_framework.decorators import action, permission_classes
from apps.core.permissions import IsModeratorOrAdmin, IsAdminUserRole

from apps.core.models import Friendship, Notification, SecurityEvent
from apps.user.serializers import can_view

from .serializers import (
    ChangePasswordSerializer,
    ForgotPasswordSerializer,
    FriendshipSerializer,
    LoginSerializer,
    NotificationSerializer,
    RegisterSerializer,
    ResetLinkSerializer,
    SetNewPasswordSerializer,
    UserBriefSerializer,
    UserMeSerializer,
    UserProfileSerializer,
    UsernameSerializer,
    VerifyEmailSerializer,
)

User = get_user_model()
Status = Friendship.SessionStatus if hasattr(Friendship, "SessionStatus") else Friendship.Status
USERNAME_REGEX = r"[\w.@+-]+"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def int_param(request, name, default, low, high):
    try:
        value = int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def find_user(username):
    if not username:
        return None
    return User.objects.filter(
        username__iexact=str(username).strip(),
        is_active=True,
    ).first()


def pair_filter(a, b):
    return Q(user=a, friend=b) | Q(user=b, friend=a)


def get_pair(a, b):
    return (
        Friendship.objects.filter(pair_filter(a, b))
        .select_related("user", "friend")
        .first()
    )


def notify(recipient, event, sender, row):
    Notification.objects.create(
        user=recipient,
        type=Notification.Type.FRIEND_REQUEST,
        payload={
            "event": event,
            "from": sender.username,
            "friendship_id": row.id,
        },
    )


def accept_request(row, me):
    row.status = Status.ACCEPTED
    row.save(update_fields=["status", "updated_at"])
    notify(row.user, "accepted", me, row)


def friends_queryset(owner, search=None):
    queryset = (
        Friendship.objects.filter(status=Status.ACCEPTED)
        .filter(Q(user=owner) | Q(friend=owner))
        .filter(user__is_active=True, friend__is_active=True)
        .select_related("user", "friend")
    )

    if search:
        queryset = queryset.filter(
            Q(user=owner, friend__username__icontains=search)
            | Q(user=owner, friend__nickname__icontains=search)
            | Q(friend=owner, user__username__icontains=search)
            | Q(friend=owner, user__nickname__icontains=search)
        )

    return queryset.order_by("-updated_at", "-id")


# ─── Email Verification & Password Reset ────────────────────────────────      
class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
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
        from_email=None,
        recipient_list=[user.email],
    )


def confirm_email(uid: str, token: str):
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
    user = User.objects.filter(username__iexact=str(username or "")[:150]).first()
    SecurityEvent.objects.create(
        event_type=SecurityEvent.EventType.LOGIN_FAILED,
        user=user,
        ip_address=request.META.get("REMOTE_ADDR") or None,
        path=request.path[:255],
    )


password_reset_token = PasswordResetTokenGenerator()


def send_password_reset_email(user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = password_reset_token.make_token(user)
    link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
    send_mail(
        subject="Reset your GameShelf password",
        message=(
            f"Hi {user.username},\n\n"
            f"Use this link to choose a new password:\n{link}\n\n"
            "If you didn't request this, you can ignore this email."
        ),
        from_email=None,
        recipient_list=[user.email],
    )


def get_reset_user(uid: str, token: str):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)), is_active=True)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    return user if password_reset_token.check_token(user, token) else None


# ─── Auth ViewSet ─────────────────────────────────────────────────────────────
class AuthViewSet(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scopes = {
        "register": "register",
        "login": "login",
        "verify_email": "verification",
        "resend_verification": "verification",
        "forgot_password": "password_forgot",
        "reset_validate": "password_reset",
        "reset_password": "password_reset",
    }

    def get_throttles(self):
        self.throttle_scope = self.throttle_scopes.get(self.action)
        return super().get_throttles()

    @action(detail=False, methods=["get"], authentication_classes=[])
    def csrf(self, request):
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
        auth_login(request, user)
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

    @action(detail=False, methods=["post"], url_path="password/forgot")
    def forgot_password(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(
            email__iexact=serializer.validated_data["email"], is_active=True
        ).first()
        if user:
            send_password_reset_email(user)
        return Response({"detail": "If an account exists for that email, a reset link has been sent."})

    @action(detail=False, methods=["post"], url_path="password/reset/validate")
    def reset_validate(self, request):
        serializer = ResetLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = dict(serializer.validated_data)
        if get_reset_user(**link) is None:
            return Response(
                {"detail": "This reset link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        request.session["pw_reset"] = link
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="password/reset")
    def reset_password(self, request):
        link = request.session.get("pw_reset")
        user = get_reset_user(**link) if link else None
        if user is None:
            return Response(
                {"detail": "Your reset session expired. Please request a new link."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = SetNewPasswordSerializer(data=request.data, context={"user": user})
        serializer.is_valid(raise_exception=True)
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        request.session.pop("pw_reset", None)
        return Response({"detail": "Password updated. You can now log in."})

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
        update_session_auth_hash(request, request.user)
        return Response({"detail": "Password changed."})


# ─── User & Consolidated Social ViewSet ───────────────────────────────────────
class UserViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Consolidated ViewSet for user profiles, me, suggested players, 
    friends, friend requests, and blocking.
    """

    queryset = User.objects.filter(is_active=True)
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "username"
    lookup_value_regex = USERNAME_REGEX

    @action(
        detail=False,
        methods=["get", "patch"],
        permission_classes=[permissions.IsAuthenticated],
        serializer_class=UserMeSerializer,
    )
    def me(self, request):
        if request.method == "PATCH":
            serializer = self.get_serializer(request.user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        return Response(self.get_serializer(request.user).data)

    # ─── Suggested Players ────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="suggested-players",
    )
    def suggested_players(self, request):
        me = request.user
        queryset = User.objects.filter(is_active=True).exclude(pk=me.pk)

        relationships = Friendship.objects.filter(Q(user=me) | Q(friend=me))
        related_ids = {me.id}
        for row in relationships.only("user_id", "friend_id"):
            related_ids.add(row.friend_id if row.user_id == me.id else row.user_id)

        queryset = queryset.exclude(pk__in=related_ids)
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(username__icontains=search) | Q(nickname__icontains=search))

        queryset = queryset.order_by("username", "id")
        total = queryset.count()
        limit = int_param(request, "limit", 50, 1, 100)
        offset = int_param(request, "offset", 0, 0, 1_000_000)

        return Response({
            "count": total,
            "results": UserBriefSerializer(
                queryset[offset:offset + limit], 
                many=True, 
                context={"request": request}
            ).data,
        })
    
    @action(
        detail=False,
        methods=["get", "post", "delete"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="favorites",
    )
    def favorites(self, request):
        me = request.user

        if request.method == "GET":
            favs = (
                UserFavoriteGame.objects.filter(user=me)
                .select_related("game")
                .prefetch_related("game__external_ids", "game__genres")
            )
            return Response(
                UserFavoriteGameSerializer(favs, many=True, context={"request": request}).data
            )

        if request.method == "POST":
            serializer = AddFavoriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            app_id = serializer.validated_data["steam_app_id"]

            try:
                game = _get_or_create_game(app_id)
            except requests.RequestException:
                return Response(
                    {"error": "Unable to fetch game details from Steam."},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

            if game is None:
                return Response(
                    {"error": f"Game not found on Steam (app id {app_id})."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            max_pos = UserFavoriteGame.objects.filter(user=me).aggregate(Max("position"))["position__max"] or 0
            position = serializer.validated_data.get("position", max_pos + 1)

            fav, created = UserFavoriteGame.objects.update_or_create(
                user=me,
                game=game,
                defaults={"position": position},
            )

            # Re-fetch with prefetch annotations for the serializer
            fav = (
                UserFavoriteGame.objects.filter(pk=fav.pk)
                .select_related("game")
                .prefetch_related("game__external_ids", "game__genres")
                .first()
            )

            return Response(
                UserFavoriteGameSerializer(fav, context={"request": request}).data,
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        if request.method == "DELETE":
            app_id = request.data.get("steam_app_id") or request.query_params.get("steam_app_id")
            if not app_id:
                return Response({"error": "steam_app_id is required."}, status=status.HTTP_400_BAD_REQUEST)

            deleted, _ = UserFavoriteGame.objects.filter(
                user=me,
                game__external_ids__source="steam",
                game__external_ids__external_id=str(app_id),
            ).delete()

            if not deleted:
                return Response({"error": "Favorite entry not found."}, status=status.HTTP_404_NOT_FOUND)

            return Response(status=status.HTTP_204_NO_CONTENT)
        
    @action(detail=True, methods=["post"], permission_classes=[IsModeratorOrAdmin])

    def sanction(self, request, pk=None):
        # Only moderators or admins can issue sanctions
        pass

    # ─── Friends & Unfriend ───────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get", "delete"],
        permission_classes=[permissions.IsAuthenticated],
        url_path=r"friends(?:/(?P<username>[\w.@+-]+))?",
    )
    def friends(self, request, username=None):
        me = request.user

        if request.method == "DELETE":
            other = find_user(username)
            row = get_pair(me, other) if other else None
            if row is None or row.status != Status.ACCEPTED:
                return Response({"detail": "You are not friends with this user."}, status=status.HTTP_404_NOT_FOUND)
            row.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # GET Friends List
        search = request.query_params.get("search", "").strip()
        queryset = friends_queryset(me, search)
        limit = int_param(request, "limit", 100, 1, 100)
        offset = int_param(request, "offset", 0, 0, 1_000_000)
        paginated_rows = queryset[offset:offset + limit]

        friend_users = [row.friend if row.user_id == me.id else row.user for row in paginated_rows]

        return Response({
            "count": queryset.count(),
            "results": UserBriefSerializer(
                friend_users, 
                many=True, 
                context={"request": request}
            ).data,
        })

    # ─── Friend Requests ──────────────────────────────────────────────────────
    @action(
        detail=False,
        methods=["get", "post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="friend-requests",
    )
    def friend_requests(self, request):
        me = request.user

        if request.method == "POST":
            serializer = UsernameSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            target = find_user(serializer.validated_data["username"])

            if target is None:
                return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
            if target.pk == me.pk:
                return Response({"detail": "You can't send a friend request to yourself."}, status=status.HTTP_400_BAD_REQUEST)

            existing = get_pair(me, target)
            if existing is not None:
                if existing.status == Status.BLOCKED:
                    message = "You blocked this user. Unblock them first." if existing.user_id == me.id else "User not found."
                    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST if existing.user_id == me.id else status.HTTP_404_NOT_FOUND)
                if existing.status == Status.ACCEPTED:
                    return Response({"detail": "You are already friends."}, status=status.HTTP_400_BAD_REQUEST)
                if existing.user_id == me.id:
                    return Response({"detail": "Friend request already sent."}, status=status.HTTP_400_BAD_REQUEST)

                # The other user already requested us: accept it automatically
                with transaction.atomic():
                    accept_request(existing, me)
                return Response(FriendshipSerializer(existing, context={"me": me}).data)

            try:
                with transaction.atomic():
                    row = Friendship.objects.create(user=me, friend=target, status=Status.PENDING)
                    notify(target, "request", me, row)
            except IntegrityError:
                return Response({"detail": "A request between you two already exists."}, status=status.HTTP_400_BAD_REQUEST)

            return Response(FriendshipSerializer(row, context={"me": me}).data, status=status.HTTP_201_CREATED)

        # GET Incoming / Outgoing Friend Requests
        direction = request.query_params.get("direction", "incoming")
        if direction == "outgoing":
            rows = Friendship.objects.filter(user=me, status=Status.PENDING)
        elif direction == "incoming":
            rows = Friendship.objects.filter(friend=me, status=Status.PENDING)
        else:
            return Response({"detail": "direction must be incoming or outgoing."}, status=status.HTTP_400_BAD_REQUEST)

        rows = rows.filter(user__is_active=True, friend__is_active=True).select_related("user", "friend").order_by("-created_at")

        return Response({
            "count": rows.count(),
            "results": FriendshipSerializer(
                rows, 
                many=True, 
                context={"me": me, "request": request}
            ).data,
        })
class UserAchievementViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = UserAchievementSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        username = self.kwargs.get("username")
        if username:
            user = get_object_or_404(User, username=username)
            return UserAchievement.objects.filter(user=user).select_related("game")
        
        # If no username provided in URL, fallback to current authenticated user
        if self.request.user.is_authenticated:
            return UserAchievement.objects.filter(user=self.request.user).select_related("game")
        return UserAchievement.objects.none()