
import requests
from concurrent.futures import ThreadPoolExecutor
from django.utils.text import slugify

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.viewsets import GenericViewSet
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import NotFound


from apps.core.models import Playlist, PlaylistGame, Game
from apps.core.models import Friendship, Notification
from apps.user.serializers import can_view

from .serializers import (
    FriendshipSerializer,
    NotificationSerializer,
    SuggestedUserSerializer,
    UsernameSerializer,
    UserBriefSerializer,
)

User = get_user_model()
Status = Friendship.Status
USERNAME_REGEX = r"[\w.@+-]+"


MAX_GENRE_SYNC = 12  # games looked up per page view

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

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

    # The requester receives the acceptance notification.
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


# ---------------------------------------------------------------------
# Suggested players
# ---------------------------------------------------------------------

class SuggestedUserViewSet(GenericViewSet):
    """
    GET /suggested-players/?search=<username>&limit=20&offset=0

    Returns active users excluding the current user and anyone with
    an existing friendship, pending request, or block relationship.
    """

    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        me = request.user

        queryset = User.objects.filter(is_active=True).exclude(pk=me.pk)

        # Exclude everyone who already has a relationship row with me.
        related_ids = Friendship.objects.filter(
            pair_filter(me, me)
        ).values_list("friend_id", flat=True)

        # Use the actual opposite user on each relationship.
        relationships = Friendship.objects.filter(
            Q(user=me) | Q(friend=me)
        )

        related_ids = set()

        for row in relationships.only("user_id", "friend_id"):
            related_ids.add(
                row.friend_id if row.user_id == me.id else row.user_id
            )

        queryset = queryset.exclude(pk__in=related_ids)

        search = request.query_params.get("search", "").strip()

        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(nickname__icontains=search)
            )

        queryset = queryset.order_by("username", "id")

        total = queryset.count()
        limit = int_param(request, "limit", 20, 1, 100)
        offset = int_param(request, "offset", 0, 0, 1_000_000)

        return Response({
            "count": total,
            "results": SuggestedUserSerializer(
                queryset[offset:offset + limit],
                many=True,
            ).data,
        })


# ---------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------

class FriendViewSet(GenericViewSet):
    """
    GET /friends/
    GET /friends/?search=<username>
    GET /friends/?username=<other_username>
    GET /friends/status/?username=<username>
    DELETE /friends/<username>/
    """

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "username"
    lookup_value_regex = USERNAME_REGEX

    def list(self, request):
        owner = request.user

        username = request.query_params.get("username")

        if username:
            owner = find_user(username)

            if owner is None:
                return Response(
                    {"detail": "User not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if not can_view(
                owner,
                request.user,
                owner.friends_visibility,
            ):
                return Response(
                    {"detail": "This player's friend list is private."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        queryset = friends_queryset(
            owner,
            request.query_params.get("search", "").strip(),
        )

        limit = int_param(request, "limit", 50, 1, 100)
        offset = int_param(request, "offset", 0, 0, 1_000_000)

        paginated_rows = queryset[offset:offset + limit]

        # Extract the other user from each friendship row (matching BlockViewSet style)
        friend_users = [
            row.friend if row.user_id == owner.id else row.user
            for row in paginated_rows
        ]

        return Response({
            "count": queryset.count(),
            "results": UserBriefSerializer(
                friend_users,
                many=True,
            ).data,
        })

    def destroy(self, request, username=None):
        other = find_user(username)
        row = get_pair(request.user, other) if other else None

        if row is None or row.status != Status.ACCEPTED:
            return Response(
                {"detail": "You are not friends with this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        row.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="status")
    def relationship(self, request):
        other = find_user(request.query_params.get("username"))

        if other is None:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if other.pk == request.user.pk:
            return Response({
                "relationship": "self",
                "friendship_id": None,
            })

        row = get_pair(request.user, other)

        if row is None:
            state = "none"
        elif row.status == Status.ACCEPTED:
            state = "friends"
        elif row.status == Status.PENDING:
            state = (
                "request_sent"
                if row.user_id == request.user.id
                else "request_received"
            )
        else:
            state = (
                "blocked_by_me"
                if row.user_id == request.user.id
                else "none"
            )

        return Response({
            "relationship": state,
            "friendship_id": row.id if row and state not in (
                "none",
                "blocked_by_me",
            ) else None,
        })


# ---------------------------------------------------------------------
# Friend requests
# ---------------------------------------------------------------------

class FriendRequestViewSet(GenericViewSet):
    serializer_class = UsernameSerializer


    """
    GET /friend-requests/?direction=incoming|outgoing
    POST /friend-requests/ {"username": "..."}
    POST /friend-requests/<id>/accept/
    POST /friend-requests/<id>/decline/
    POST /friend-requests/<id>/cancel/
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    def get_serializer_class(self):
        if self.action in ("accept", "decline", "cancel", "list"):
            return FriendshipSerializer
        return UsernameSerializer
    
    def get_throttles(self):
        self.throttle_scope = (
            "friend_request" if self.action == "create" else None
        )
        return super().get_throttles()

    def list(self, request):
        me = request.user
        direction = request.query_params.get("direction", "incoming")

        if direction == "outgoing":
            rows = Friendship.objects.filter(
                user=me,
                status=Status.PENDING,
            )
        elif direction == "incoming":
            rows = Friendship.objects.filter(
                friend=me,
                status=Status.PENDING,
            )
        else:
            return Response(
                {"detail": "direction must be incoming or outgoing."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows = (
            rows.filter(user__is_active=True, friend__is_active=True)
            .select_related("user", "friend")
            .order_by("-created_at")
        )

        return Response({
            "count": rows.count(),
            "results": FriendshipSerializer(
                rows,
                many=True,
                context={"me": me},
            ).data,
        })

    def create(self, request):
        serializer = UsernameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        me = request.user
        target = find_user(serializer.validated_data["username"])

        if target is None:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if target.pk == me.pk:
            return Response(
                {"detail": "You can't send a friend request to yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = get_pair(me, target)

        if existing is not None:
            if existing.status == Status.BLOCKED:
                message = (
                    "You blocked this user. Unblock them first."
                    if existing.user_id == me.id
                    else "User not found."
                )

                return Response(
                    {"detail": message},
                    status=(
                        status.HTTP_400_BAD_REQUEST
                        if existing.user_id == me.id
                        else status.HTTP_404_NOT_FOUND
                    ),
                )

            if existing.status == Status.ACCEPTED:
                return Response(
                    {"detail": "You are already friends."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if existing.user_id == me.id:
                return Response(
                    {"detail": "Friend request already sent."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # The other user already requested us: accept it.
            with transaction.atomic():
                accept_request(existing, me)

            return Response(
                FriendshipSerializer(
                    existing,
                    context={"me": me},
                ).data
            )

        try:
            with transaction.atomic():
                row = Friendship.objects.create(
                    user=me,
                    friend=target,
                    status=Status.PENDING,
                )
                notify(target, "request", me, row)

        except IntegrityError:
            return Response(
                {"detail": "A request between you two already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            FriendshipSerializer(
                row,
                context={"me": me},
            ).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        with transaction.atomic():
            row = get_object_or_404(
                Friendship.objects.select_for_update(),
                pk=pk,
                friend=request.user,
                status=Status.PENDING,
            )
            accept_request(row, request.user)

        return Response(
            FriendshipSerializer(
                row,
                context={"me": request.user},
            ).data
        )

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        row = get_object_or_404(
            Friendship,
            pk=pk,
            friend=request.user,
            status=Status.PENDING,
        )
        row.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        row = get_object_or_404(
            Friendship,
            pk=pk,
            user=request.user,
            status=Status.PENDING,
        )
        row.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------

class BlockViewSet(GenericViewSet):
    """
    GET /blocks/
    POST /blocks/ {"username": "..."}
    DELETE /blocks/<username>/
    """

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "username"
    lookup_value_regex = USERNAME_REGEX

    def list(self, request):
        rows = (
            Friendship.objects.filter(
                user=request.user,
                status=Status.BLOCKED,
            )
            .select_related("friend")
            .order_by("-updated_at")
        )

        user = [row.friend for row in rows]

        return Response({
            "count": len(user),
            "results": UserBriefSerializer(user, many=True).data,
        })

    def create(self, request):
        serializer = UsernameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        me = request.user
        target = find_user(serializer.validated_data["username"])

        if target is None:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if target.pk == me.pk:
            return Response(
                {"detail": "You can't block yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = get_pair(me, target)

        if existing and existing.status == Status.BLOCKED:
            if existing.user_id == me.id:
                return Response(UserBriefSerializer(target).data)

            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        with transaction.atomic():
            Friendship.objects.filter(pair_filter(me, target)).delete()

            row = Friendship.objects.create(
                user=me,
                friend=target,
                status=Status.BLOCKED,
            )

        return Response(
            UserBriefSerializer(target).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, username=None):
        other = find_user(username)

        row = (
            Friendship.objects.filter(
                user=request.user,
                friend=other,
                status=Status.BLOCKED,
            ).first()
            if other
            else None
        )

        if row is None:
            return Response(
                {"detail": "You haven't blocked this user."},
                status=status.HTTP_404_NOT_FOUND,
            )

        row.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------

class NotificationViewSet(GenericViewSet):
    """
    GET /notifications/?unread=1
    POST /notifications/<id>/read/
    POST /notifications/read-all/
    """

    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        mine = Notification.objects.filter(user=request.user)

        queryset = (
            mine.filter(read_at__isnull=True)
            if request.query_params.get("unread")
            else mine
        )

        limit = int_param(request, "limit", 30, 1, 100)

        return Response({
            "unread_count": mine.filter(read_at__isnull=True).count(),
            "results": NotificationSerializer(
                queryset.order_by("-created_at")[:limit],
                many=True,
            ).data,
        })

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        note = get_object_or_404(
            Notification,
            pk=pk,
            user=request.user,
        )

        if note.read_at is None:
            note.read_at = timezone.now()
            note.save(update_fields=["read_at"])

        return Response(NotificationSerializer(note).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        updated = Notification.objects.filter(
            user=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())

        return Response({"marked_read": updated})

# ---------------------------------------------------------------------
#For game list
# ---------------------------------------------------------------------



class PlaylistPagination(LimitOffsetPagination):
    default_limit = 12
    max_limit = 50


def _steam_id(game):
    ext = next((e for e in game.external_ids.all() if e.source == "steam"), None)
    try:
        return int(ext.external_id) if ext else None
    except (TypeError, ValueError):
        return None


def _fetch_steam_genres(appid):
    """Genre names for one Steam app, e.g. ["Action", "Indie"]. [] on any failure."""
    try:
        res = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": appid, "filters": "genres", "l": "english"},
            timeout=8,
        )
        entry = res.json().get(str(appid)) or {}
        info = entry.get("data")
        if not entry.get("success") or not isinstance(info, dict):
            return []
        return [
            g["description"].strip()
            for g in info.get("genres", [])
            if g.get("description")
        ]
    except (requests.RequestException, ValueError, KeyError, AttributeError):
        return []


def _fill_missing_genres(links):
    """Fetch genres from Steam for games that have none. True if anything was saved."""
    todo = {}
    for link in links:
        game = link.game
        if game.id in todo or game.genres.all():
            continue
        appid = _steam_id(game)
        if appid:
            todo[game.id] = (game, appid)
        if len(todo) >= MAX_GENRE_SYNC:
            break

    if not todo:
        return False

    # Steam requests run in parallel; database writes stay in this thread.
    with ThreadPoolExecutor(max_workers=6) as pool:
        names_list = list(pool.map(lambda item: _fetch_steam_genres(item[1]), todo.values()))

    Genre = Game._meta.get_field("genres").related_model
    has_slug = any(f.name == "slug" for f in Genre._meta.get_fields())

    saved = False
    for (game, _appid), names in zip(todo.values(), names_list):
        genres = []
        for name in names:
            genre = Genre.objects.filter(name__iexact=name).first()
            if genre is None:
                kwargs = {"name": name}
                if has_slug:
                    kwargs["slug"] = slugify(name)
                genre = Genre.objects.create(**kwargs)
            genres.append(genre)

        if genres:
            game.genres.add(*genres)
            saved = True

    return saved


def _serialize_playlists(playlists, genre="", platform="", sync=False):
    """Playlist rows -> dicts matching the frontend's Playlist type.

    With genre/platform set, `games` only holds the matching games,
    while `game_count` stays the playlist's full size.
    With sync=True, games that have no genres get them from Steam first.
    """

    def load_links():
        return list(
            PlaylistGame.objects.filter(playlist_id__in=[p.id for p in playlists])
            .select_related("game")
            .prefetch_related("game__external_ids", "game__genres")
            .order_by("position")
        )

    links = load_links()

    if sync and _fill_missing_genres(links):
        links = load_links()  # reload so the new genres show up

    genre = genre.strip().lower()
    platform = platform.strip().lower()

    platform_ids = None
    if platform in ("windows", "mac", "linux"):
        platform_ids = set(
            Game.objects.filter(
                id__in=[l.game_id for l in links],
                platforms__slug=platform,  # <- adjust to your Game model
            ).values_list("id", flat=True)
        )

    games_by_playlist = {}
    totals = {}
    for link in links:
        totals[link.playlist_id] = totals.get(link.playlist_id, 0) + 1

        if genre and genre not in {g.name.lower() for g in link.game.genres.all()}:
            continue
        if platform_ids is not None and link.game_id not in platform_ids:
            continue

        games_by_playlist.setdefault(link.playlist_id, []).append({
            "id": link.game.id,
            "title": link.game.title,
            "steam_appid": _steam_id(link.game),
            "cover_url": link.game.cover_image_url,
            "genres": [g.name for g in link.game.genres.all()],
            "position": link.position,
            "note": getattr(link, "note", None),
        })

    results = []
    for p in playlists:
        results.append({
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "visibility": p.visibility,
            "owner": {
                "id": p.user.id,
                "username": p.user.username,
                "nickname": getattr(p.user, "nickname", None),
                "avatar_url": getattr(p.user, "avatar_url", None),
            },
            "game_count": totals.get(p.id, 0),
            "like_count": 0,        # TODO: wire to your likes table
            "liked_by_me": False,   # TODO
            "games": games_by_playlist.get(p.id, []),
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        })
    return results

class CommunityPlaylistViewSet(GenericViewSet):
    """
    GET /playlists/?search=&user=&genre=&platform=&sort=&limit=12&offset=0
    GET /playlists/genres/
    GET /playlists/<id>/
    """

    permission_classes = [permissions.AllowAny]
    pagination_class = PlaylistPagination

    SORTS = {
        "newest": "-id",
        "oldest": "id",
        "updated": "-updated_at",
        "title": "title",
    }

    def get_queryset(self):
        queryset = Playlist.objects.filter(
            deleted_at__isnull=True,
            visibility="public",
        ).select_related("user")

        params = self.request.query_params

        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(title__icontains=search)

        username = (params.get("user") or "").strip()
        if username:
            queryset = queryset.filter(user__username=username)

        # A playlist matches if at least one of its games has the genre.
        genre = (params.get("genre") or "").strip()
        if genre:
            queryset = queryset.filter(
                id__in=PlaylistGame.objects.filter(
                    game__genres__name__iexact=genre
                ).values("playlist_id")
            )

        platform = (params.get("platform") or "").strip().lower()
        if platform in ("windows", "mac", "linux"):
            queryset = queryset.filter(
                id__in=PlaylistGame.objects.filter(
                    game__platforms__slug=platform  # <- adjust to your Game model
                ).values("playlist_id")
            )

        order = self.SORTS.get((params.get("sort") or "").strip().lower(), "-id")
        return queryset.order_by(order, "-id")

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        params = request.query_params
        return self.get_paginated_response(
            _serialize_playlists(
                page,
                genre=params.get("genre") or "",
                platform=params.get("platform") or "",
            )
        )

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def mine(self, request):
        """GET /social/playlists/mine/ -> only the logged-in user's playlists (public + private)."""
        queryset = (
            Playlist.objects.filter(user=request.user, deleted_at__isnull=True)
            .select_related("user")
            .order_by("-updated_at", "-id")
        )
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(_serialize_playlists(page))

    @action(detail=False, methods=["get"])
    def genres(self, request):
        """Genres that actually exist in public playlists (for the filter dropdown)."""
        names = (
            PlaylistGame.objects.filter(
                playlist__deleted_at__isnull=True,
                playlist__visibility="public",
                game__genres__isnull=False,
            )
            .values_list("game__genres__name", flat=True)
            .distinct()
            .order_by("game__genres__name")
        )
        return Response(list(names))

    def retrieve(self, request, pk=None):
        playlist = get_object_or_404(
            Playlist.objects.select_related("user"),
            pk=pk,
            deleted_at__isnull=True,
        )

        is_owner = request.user.is_authenticated and playlist.user_id == request.user.id
        if playlist.visibility == "private" and not is_owner:
            raise NotFound("Playlist not found.")

        return Response(_serialize_playlists([playlist], sync=True)[0])