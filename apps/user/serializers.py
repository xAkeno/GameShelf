import os
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q
from rest_framework import serializers

from apps.core.models import Friendship, Rating, Review, UserGameList, Notification

User = get_user_model()


# ─── Helpers (privacy rules and counts for profiles) ──────────────────────────
def are_friends(a, b) -> bool:
    if a is None or b is None or not a.is_authenticated:
        return False
    return (
        Friendship.objects.filter(status=Friendship.Status.ACCEPTED)
        .filter(Q(user=a, friend=b) | Q(user=b, friend=a))
        .exists()
    )


def can_view(owner, viewer, visibility: str) -> bool:
    """Whether `viewer` may see something `owner` marked public / friends / private."""
    if viewer is not None and viewer.is_authenticated:
        if viewer.pk == owner.pk or getattr(viewer, "is_moderator", False):
            return True
    if visibility == "public":
        return True
    if visibility == "friends":
        return are_friends(viewer, owner)
    return False


def count_friends(user) -> int:
    return (
        Friendship.objects.filter(status=Friendship.Status.ACCEPTED)
        .filter(Q(user=user) | Q(friend=user))
        .count()
    )


def library_counts(user) -> dict:
    """{'playing': 3, 'completed': 10, ...} with every status present."""
    counts = {status: 0 for status in UserGameList.Status.values}
    rows = UserGameList.objects.filter(user=user).values("status").annotate(total=Count("id"))
    for row in rows:
        counts[row["status"]] = row["total"]
    return counts


def run_password_validators(password, user=None):
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages))


# ─── Base Mixin for Absolute Media URLs ────────────────────────────────────────
class AbsoluteMediaURLMixin:
    """Ensures avatar_url and banner_url are returned as absolute URIs when a request context is present."""
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        for field in ("avatar_url", "banner_url"):
            url = data.get(field)
            if request and url and url.startswith(settings.MEDIA_URL):
                data[field] = request.build_absolute_uri(url)

        return data


# ─── User Brief & Friend Serializers ──────────────────────────────────────────
class UserBriefSerializer(AbsoluteMediaURLMixin, serializers.ModelSerializer):
    """Compact user profile representation for friends lists, search, suggestions, and blocks."""
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "nickname", "avatar_url", "bio"]

    def get_avatar_url(self, obj):
        if not obj.avatar_url:
            return None
        
        request = self.context.get("request")
        if request and obj.avatar_url.startswith(settings.MEDIA_URL):
            return request.build_absolute_uri(obj.avatar_url)
        
        # If media URL is stored relative without leading domain
        if request and not obj.avatar_url.startswith("http"):
            return request.build_absolute_uri(obj.avatar_url)
            
        return obj.avatar_url


class FriendshipSerializer(serializers.ModelSerializer):
    """Represent a friendship relationship or pending request from the logged-in user's perspective."""
    user = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()

    class Meta:
        model = Friendship
        fields = ["id", "user", "status", "direction", "created_at", "updated_at"]

    def get_user(self, obj):
        me = self.context.get("me")
        if not me:
            return UserBriefSerializer(obj.friend, context=self.context).data
        other = obj.friend if obj.user_id == me.id else obj.user
        return UserBriefSerializer(other, context=self.context).data

    def get_direction(self, obj):
        me = self.context.get("me")
        if not me or obj.status != Friendship.Status.PENDING:
            return None
        return "outgoing" if obj.user_id == me.id else "incoming"


class FriendshipSerializer(serializers.ModelSerializer):
    """Represent a friendship relationship or pending request from the logged-in user's perspective."""
    user = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()

    class Meta:
        model = Friendship
        fields = ["id", "user", "status", "direction", "created_at", "updated_at"]

    def get_user(self, obj):
        me = self.context.get("me")
        if not me:
            return UserBriefSerializer(obj.friend).data
        other = obj.friend if obj.user_id == me.id else obj.user
        return UserBriefSerializer(other, context=self.context).data

    def get_direction(self, obj):
        me = self.context.get("me")
        if not me or obj.status != Friendship.Status.PENDING:
            return None
        return "outgoing" if obj.user_id == me.id else "incoming"


# ─── Register / Login / Password ──────────────────────────────────────────────
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ["username", "email", "password"]
        extra_kwargs = {
            "username": {"min_length": 3},
            "email": {"required": True, "allow_blank": False},
        }

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        candidate = User(username=attrs.get("username"), email=attrs.get("email"))
        try:
            run_password_validators(attrs["password"], user=candidate)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"password": exc.detail})
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class VerifyEmailSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("Incorrect username or password.")
        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate_new_password(self, value):
        run_password_validators(value, user=self.context["request"].user)
        return value

    def validate(self, attrs):
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError(
                {"new_password": ["Choose a password you haven't used here before."]}
            )
        return attrs


# ─── Profiles ─────────────────────────────────────────────────────────────────
class UserMeSerializer(AbsoluteMediaURLMixin, serializers.ModelSerializer):
    """The logged-in user's own account. Profile and privacy fields are editable."""

    email_verified = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField(source="date_joined", read_only=True)
    avatar_file = serializers.ImageField(write_only=True, required=False)
    banner_file = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = User
        fields = [
            "id", "username", "email", "email_verified", "role",
            "nickname", "bio", "avatar_url", "banner_url",
            "library_visibility", "friends_visibility", "created_at",
            "avatar_file", "banner_file",
        ]
        read_only_fields = ["id", "username", "email", "role"]

    def get_email_verified(self, obj) -> bool:
        return obj.email_verified_at is not None

    def update(self, instance, validated_data):
        avatar = validated_data.pop("avatar_file", None)
        banner = validated_data.pop("banner_file", None)

        instance = super().update(instance, validated_data)

        if avatar:
            ext = os.path.splitext(avatar.name)[1].lower() or ".jpg"
            filename = f"avatars/{uuid.uuid4().hex}{ext}"
            instance.avatar_url = default_storage.url(
                default_storage.save(filename, avatar)
            )

        if banner:
            ext = os.path.splitext(banner.name)[1].lower() or ".jpg"
            filename = f"banners/{uuid.uuid4().hex}{ext}"
            instance.banner_url = default_storage.url(
                default_storage.save(filename, banner)
            )

        if avatar or banner:
            instance.save(update_fields=["avatar_url", "banner_url"])

        return instance


class UserProfileSerializer(AbsoluteMediaURLMixin, serializers.ModelSerializer):
    """What other people see. Email and role are excluded based on privacy settings."""

    joined = serializers.DateTimeField(source="date_joined", read_only=True)
    friend_count = serializers.SerializerMethodField()
    library = serializers.SerializerMethodField()
    ratings_count = serializers.SerializerMethodField()
    reviews_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "username", "nickname", "bio", "avatar_url", "banner_url", "joined",
            "friend_count", "library", "ratings_count", "reviews_count",
        ]

    def _viewer(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def get_friend_count(self, obj):
        if can_view(obj, self._viewer(), obj.friends_visibility):
            return count_friends(obj)
        return None

    def get_library(self, obj):
        if can_view(obj, self._viewer(), obj.library_visibility):
            return library_counts(obj)
        return None

    def get_ratings_count(self, obj) -> int:
        return Rating.objects.filter(user=obj).count()

    def get_reviews_count(self, obj) -> int:
        return Review.objects.filter(
            user=obj, deleted_at__isnull=True, status=Review.Status.PUBLISHED
        ).count()


# ─── Password Reset & Utility Serializers ─────────────────────────────────────
class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetLinkSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()


class SetNewPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        run_password_validators(value, user=self.context["user"])
        return value


class UsernameSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "payload", "read_at", "created_at"]