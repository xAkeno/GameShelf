import re
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q, Max
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import mixins, permissions, status, viewsets, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
from apps.library.views import _get_or_create_game
from apps.core.models import UserGameList
from apps.core.models import UserFavoriteGame, Game, Friendship, Notification, SecurityEvent
from apps.core.models.users import UserAchievement
from apps.core.permissions import IsModeratorOrAdmin
from apps.core.models.users import UserSanction, SanctionAppeal
from apps.library.views import _get_or_create_game
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
    UserAchievementSerializer,
    UserFavoriteGameSerializer,
    AddFavoriteSerializer,
    SanctionAppealSerializer,
    UserSanctionSerializer,
)

User = get_user_model()
Status = Friendship.SessionStatus if hasattr(Friendship, "SessionStatus") else Friendship.Status
USERNAME_REGEX = r"[\w.@+-]+"

GameExternalID = None
for model_name in ["GameExternalID", "ExternalGameID", "ExternalID"]:
    try:
        from apps.core import models as core_models
        if hasattr(core_models, model_name):
            GameExternalID = getattr(core_models, model_name)
            break
    except ImportError:
        pass

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


# ─── Steam Import Services ────────────────────────────────────────────────────

def resolve_steam_id(url_or_id: str) -> str:
    cleaned = url_or_id.strip().rstrip("/")
    
    if re.match(r"^\d{17}$", cleaned):
        return cleaned
    
    match_profile = re.search(r"steamcommunity\.com/profiles/(\d{17})", cleaned)
    if match_profile:
        return match_profile.group(1)
    
    match_vanity = re.search(r"steamcommunity\.com/id/([^/]+)", cleaned)
    vanity = match_vanity.group(1) if match_vanity else cleaned.split("/")[-1]
        
    api_key = getattr(settings, "STEAM_API_KEY", "")
    if not api_key:
        raise ValidationError("Steam API key is not configured on the server.")
        
    url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={api_key}&vanityurl={vanity}"
    res = requests.get(url).json()
    
    if res.get("response", {}).get("success") == 1:
        return res["response"]["steamid"]
        
    raise ValidationError("Could not resolve Steam profile. Make sure the link is correct.")

import datetime
from django.utils import timezone

import datetime
from django.utils import timezone

def import_steam_user_data(user, profile_link: str):
    steam_id = resolve_steam_id(profile_link)
    api_key = getattr(settings, "STEAM_API_KEY", "")
    
    if not api_key:
        raise ValidationError("Steam API key is not configured on the server.")

    # 1. Fetch Owned Games
    games_url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={api_key}&steamid={steam_id}&include_appinfo=true&include_played_free_games=true&format=json"
    response = requests.get(games_url).json()
    
    game_response = response.get("response", {})
    if not game_response or "games" not in game_response:
        raise ValidationError("Could not fetch games. Ensure your Steam Game Details privacy setting is set to Public.")
        
    imported_count = 0
    achievements_count = 0
    now_timestamp = timezone.now().timestamp()
    one_week_ago = now_timestamp - (7 * 24 * 60 * 60)

    for sg in game_response["games"]:
        app_id = sg["appid"]
        playtime_mins = sg.get("playtime_forever", 0)
        last_played_timestamp = sg.get("rtime_last_played", 0)
        
        game = _get_or_create_game(app_id)
        if not game:
            continue
            
        # Smart status assignment
        default_status = "plan_to_play"
        if last_played_timestamp > one_week_ago or playtime_mins > 300:
            default_status = "playing"

        entry, _ = UserGameList.objects.get_or_create(
            user=user, 
            game=game, 
            defaults={"status": default_status}
        )
        imported_count += 1

        # 2. Fetch Player Achievements for Played Games
        if playtime_mins > 0:
            ach_url = f"https://api.steampowered.com/ISteamUserStats/GetPlayerAchievements/v0001/?appid={app_id}&key={api_key}&steamid={steam_id}"
            try:
                ach_res = requests.get(ach_url, timeout=3).json()
                player_stats = ach_res.get("playerstats", {})
                if player_stats.get("success") and "achievements" in player_stats:
                    for ach in player_stats["achievements"]:
                        if ach.get("achieved") == 1:
                            unlock_time = ach.get("unlocktime")
                            unlocked_at = (
                                timezone.datetime.fromtimestamp(unlock_time, tz=timezone.utc)
                                if unlock_time
                                else timezone.now()
                            )
                            UserAchievement.objects.get_or_create(
                                user=user,
                                game=game,
                                achievement_api_name=ach["apiname"],
                                defaults={
                                    "title": ach.get("name", ach["apiname"]),
                                    "description": ach.get("description", ""),
                                    "unlocked_at": unlocked_at,
                                },
                            )
                            achievements_count += 1
            except Exception:
                # Silently continue if a specific game lacks achievement stats
                pass
        
    return {
        "detail": f"Successfully imported {imported_count} games and {achievements_count} unlocked achievements from Steam!"
    }

class ImportSteamLibraryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        profile_url = request.data.get("profile_url")
        if not profile_url:
            raise ValidationError({"profile_url": "A Steam profile link or SteamID is required."})
        
        try:
            result = import_steam_user_data(request.user, profile_url)
            return Response(result, status=status.HTTP_200_OK)
        except ValidationError as e:
            error_msg = e.detail[0] if isinstance(e.detail, list) else e.detail
            return Response({"detail": error_msg}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"detail": f"An unexpected error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Email Verification & Password Reset ──────────────────────────────────────
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
        pass
    @action(
        detail=False,
        methods=["get"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="achievements",
    )
    def my_achievements(self, request):
        queryset = UserAchievement.objects.filter(user=request.user).select_related("game")
        serializer = UserAchievementSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get"],
        permission_classes=[permissions.AllowAny],
        url_path="achievements",
    )
    def user_achievements(self, request, username=None):
        target_user = self.get_object()
        queryset = UserAchievement.objects.filter(user=target_user).select_related("game")
        serializer = UserAchievementSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)

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
    
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="block",
    )
    def block_user(self, request, pk=None):
        target_user = self.get_object()
        me = request.user
        
        if target_user == me:
            return Response({"detail": "You cannot block yourself."}, status=status.HTTP_400_BAD_REQUEST)
        
        with transaction.atomic():
            # Find existing friendship or create a blocked relationship
            friendship = Friendship.objects.filter(
                (Q(user=me) & Q(friend=target_user)) | (Q(user=target_user) & Q(friend=me))
            ).first()
            
            if friendship:
                friendship.status = Status.BLOCKED
                friendship.user = me
                friendship.friend = target_user
                friendship.save(update_fields=["status", "user", "friend", "updated_at"])
            else:
                Friendship.objects.create(
                    user=me,
                    friend=target_user,
                    status=Status.BLOCKED
                )
                
        return Response({"detail": "User blocked successfully."})

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[permissions.IsAuthenticated],
        url_path="report",
    )
    def report_user(self, request, pk=None):
        target_user = self.get_object()
        me = request.user
        
        if target_user == me:
            return Response({"detail": "You cannot report yourself."}, status=status.HTTP_400_BAD_REQUEST)
            
        reason = request.data.get("reason", "No reason provided")
        
        # Log or create report entry (or handle security event/notification)
        # SecurityEvent or a custom Report model can be integrated here if available
        
        return Response({"detail": "User reported successfully. Thank you for keeping the community safe."})
    
class FriendshipViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FriendshipSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        me = self.request.user
        return Friendship.objects.filter(Q(user=me) | Q(friend=me)).select_related("user", "friend")

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        friendship = self.get_object()
        me = request.user
        
        if friendship.friend != me:
            return Response({"detail": "You cannot accept this request."}, status=status.HTTP_403_FORBIDDEN)
        if friendship.status != Status.PENDING:
            return Response({"detail": "This request is not pending."}, status=status.HTTP_400_BAD_REQUEST)
        
        with transaction.atomic():
            accept_request(friendship, me)
            
        return Response(FriendshipSerializer(friendship, context={"me": me}).data)

    @action(detail=True, methods=["post", "delete"], url_path="reject")
    def reject(self, request, pk=None):
        friendship = self.get_object()
        me = request.user
        
        if friendship.friend != me and friendship.user != me:
            return Response({"detail": "Unauthorized."}, status=status.HTTP_403_FORBIDDEN)
            
        friendship.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="block")
    def block(self, request, pk=None):
        friendship = self.get_object()
        me = request.user
        
        with transaction.atomic():
            friendship.status = Status.BLOCKED
            friendship.user = me
            friendship.save(update_fields=["status", "user", "updated_at"])
            
        return Response({"detail": "User blocked successfully."})
    
class BlockViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FriendshipSerializer
    lookup_field = "friend__username"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return Friendship.objects.filter(user=self.request.user, status=Status.BLOCKED).select_related("friend")

    def create(self, request, *args, **kwargs):
        username = request.data.get("username")
        target_user = get_object_or_404(User, username=username)
        me = request.user
        
        if target_user == me:
            return Response({"detail": "You cannot block yourself."}, status=status.HTTP_400_BAD_REQUEST)
            
        friendship, _ = Friendship.objects.update_or_create(
            user=me,
            friend=target_user,
            defaults={"status": Status.BLOCKED}
        )
        return Response(FriendshipSerializer(friendship, context={"me": me}).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        username = kwargs.get("friend__username")
        instance = get_object_or_404(Friendship, user=request.user, friend__username=username, status=Status.BLOCKED)
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class ReportViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["post", "head", "options"]

    def create(self, request, *args, **kwargs):
        username = request.data.get("username")
        reason = request.data.get("reason", "No reason provided")
        details = request.data.get("details", "")
        
        target_user = get_object_or_404(User, username=username)
        me = request.user
        
        if target_user == me:
            return Response({"detail": "You cannot report yourself."}, status=status.HTTP_400_BAD_REQUEST)
            
        # Optional: Save to a Report model if your project tracks user reports in the database
        
        return Response({"detail": "User reported successfully. Thank you for keeping the community safe."}, status=status.HTTP_201_CREATED)
    
class MySanctionsListView(generics.ListAPIView):
    """GET /api/v1/sanctions/my/ — List all sanctions belonging to the logged-in user."""
    serializer_class = UserSanctionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Retrieve sanctions belonging to the current user (excluding revoked ones if desired)
        return UserSanction.objects.filter(
            user=self.request.user,
            revoked_at__isnull=True
        ).order_by("-created_at")


class SanctionAppealCreateView(generics.CreateAPIView):
    """POST /api/v1/sanctions/appeals/ — Submit an appeal against a specific sanction."""
    serializer_class = SanctionAppealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context