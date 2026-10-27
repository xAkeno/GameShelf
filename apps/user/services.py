import re
import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from apps.core.models import Game, ExternalGameID
from apps.library.models import UserGameEntry

# ─── Steam Import Helper Functions ───────────────────────────────────────────

def resolve_steam_id(url_or_id: str) -> str:
    cleaned = url_or_id.strip().rstrip("/")
    
    # If it's already a 17-digit SteamID64
    if re.match(r"^\d{17}$", cleaned):
        return cleaned
    
    # Extract from /profiles/76561198...
    match_profile = re.search(r"steamcommunity\.com/profiles/(\d{17})", cleaned)
    if match_profile:
        return match_profile.group(1)
    
    # Extract vanity URL from /id/customname
    match_vanity = re.search(r"steamcommunity\.com/id/([^/]+)", cleaned)
    if not match_vanity:
        vanity = cleaned.split("/")[-1]
    else:
        vanity = match_vanity.group(1)
        
    api_key = getattr(settings, "STEAM_API_KEY", "")
    if not api_key:
        raise ValidationError("Steam API key is not configured on the server.")
        
    url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v0001/?key={api_key}&vanityurl={vanity}"
    res = requests.get(url).json()
    
    if res.get("response", {}).get("success") == 1:
        return res["response"]["steamid"]
        
    raise ValidationError("Could not resolve Steam profile. Make sure the link is correct.")

def import_steam_user_data(user, profile_link: str):
    steam_id = resolve_steam_id(profile_link)
    api_key = getattr(settings, "STEAM_API_KEY", "")
    
    if not api_key:
        raise ValidationError("Steam API key is not configured on the server.")

    games_url = f"https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/?key={api_key}&steamid={steam_id}&include_appinfo=true&include_played_free_games=true&format=json"
    response = requests.get(games_url).json()
    
    game_response = response.get("response", {})
    if not game_response or "games" not in game_response:
        raise ValidationError("Could not fetch games. Ensure your Steam Game Details privacy setting is set to Public.")
        
    imported_count = 0
    for sg in game_response["games"]:
        app_id = sg["appid"]
        title = sg.get("name", f"Steam Game {app_id}")
        playtime_mins = sg.get("playtime_forever", 0)
        
        ext_id, created = ExternalGameID.objects.get_or_create(
            source="steam",
            external_id=str(app_id),
            defaults={"game": Game.objects.create(title=title)}
        )
        game = ext_id.game
        
        entry, _ = UserGameEntry.objects.get_or_create(user=user, game=game, defaults={"status": "plan_to_play"})
        if playtime_mins > 60 and entry.status == "plan_to_play":
            entry.status = "playing"
            entry.save(update_fields=["status"])
            
        imported_count += 1
        
    return {"detail": f"Successfully imported {imported_count} games from Steam."}


# ─── Steam Import API View ───────────────────────────────────────────────────

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