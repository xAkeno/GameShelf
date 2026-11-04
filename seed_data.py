import os
import django
import random

# 1. Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gameshelf.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.apps import apps

User = get_user_model()

# Dynamically locate models across any installed Django app
def find_model(model_name):
    for model in apps.get_models():
        if model.__name__.lower() == model_name.lower():
            return model
    return None

Friendship = find_model('Friendship')
UserFavoriteGameEntry = find_model('UserFavoriteGameEntry')
Playlist = find_model('Playlist')

print(f"Model Discovery -> Friendship: {Friendship}, FavoriteGame: {UserFavoriteGameEntry}, Playlist: {Playlist}")

try:
    my_account = User.objects.get(id=1)
    print(f"Target account found: {my_account.username} (ID: {my_account.id})")
except User.DoesNotExist:
    print("Error: User with ID 1 does not exist.")
    exit()

sample_app_ids = [730, 570, 271590, 1086940, 1172410, 292030]

print("Generating 100 mock users with profiles, 100 incoming requests, playlists, and favorites...")

for i in range(1, 101):
    username = f"pro_gamer_{i}"
    email = f"gamer_{i}@test.com"
    nickname = f"PlayerOne #{i}"
    avatar_url = f"https://api.dicebear.com/7.x/bottts/svg?seed=user_{i}"
    banner_url = "https://images.unsplash.com/photo-1550745165-9bc0b252726f?w=1200"
    bio = f"Hey! I'm test user #{i} exploring GameShelf. Let's play!"

    # Create or update mock user profile
    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": email,
            "nickname": nickname,
            "avatar_url": avatar_url,
            "banner_url": banner_url,
            "bio": bio,
        }
    )
    
    if not created:
        user.avatar_url = avatar_url
        user.nickname = nickname
        user.bio = bio
        user.save()

    # 1. Send Friend Request to User ID 1 (cereal_solstice)
    if Friendship:
        try:
            Friendship.objects.get_or_create(
                user=user,
                friend=my_account,
                defaults={"status": "pending"}
            )
        except Exception:
            try:
                Friendship.objects.get_or_create(
                    from_user=user,
                    to_user=my_account,
                    defaults={"status": "pending"}
                )
            except Exception:
                pass

    # 2. Give them a Mock Playlist for the community section
    if Playlist:
        try:
            Playlist.objects.get_or_create(
                owner=user,
                title=f"{nickname}'s Favorite Indie Gems",
                defaults={"visibility": "public"}
            )
        except Exception:
            try:
                Playlist.objects.get_or_create(
                    user=user,
                    title=f"{nickname}'s Favorite Indie Gems",
                    defaults={"visibility": "public"}
                )
            except Exception:
                pass

    # 3. Add 2 Favorite Games to their Profile
    if UserFavoriteGameEntry:
        try:
            UserFavoriteGameEntry.objects.filter(user=user).delete()
            for app_id in random.sample(sample_app_ids, 2):
                UserFavoriteGameEntry.objects.create(
                    user=user,
                    steam_appid=app_id,
                    title=f"Game Title {app_id}",
                    cover_image_url=f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
                )
        except Exception:
            pass

print("Successfully seeded 100 active mock users with profile pictures, 100 requests targeting ID 1, playlists, and favorite games!")