import re
import time
from datetime import datetime

import requests
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Game, Genre, Platform
from apps.games.serializers import get_steam_game_details


def get_external_id_model():
    return Game._meta.get_field("external_ids").related_model


def get_external_id_fields(model):
    fields = {field.name for field in model._meta.get_fields()}

    source_field = next(
        (name for name in ("source", "provider", "platform", "store")
         if name in fields),
        None,
    )
    value_field = next(
        (name for name in (
            "external_id", "value", "identifier", "app_id",
            "steam_id", "key"
        ) if name in fields),
        None,
    )

    if not value_field:
        raise RuntimeError("Could not identify the external ID field.")

    return source_field, value_field


def parse_release_date(value):
    if not value:
        return None

    match = re.search(r"\b(?:19|20)\d{2}\b", value)
    if not match:
        return None

    # Steam sometimes provides only a year or a human-readable date.
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass

    return datetime(int(match.group()), 1, 1).date()


class Command(BaseCommand):
    help = "Import metadata for existing games linked to Steam app IDs."

    def handle(self, *args, **options):
        external_model = get_external_id_model()
        source_field, value_field = get_external_id_fields(external_model)

        links = external_model.objects.all()

        if source_field:
            links = links.filter(**{f"{source_field}__iexact": "steam"})

        links = list(links.select_related("game"))

        self.stdout.write(f"Found {len(links)} Steam game links.")

        updated = 0
        skipped = 0
        failed = 0

        for link in links:
            app_id = getattr(link, value_field)

            try:
                details = get_steam_game_details(int(app_id))

                if not details:
                    skipped += 1
                    continue

                with transaction.atomic():
                    game = link.game
                    locked = set(game.locked_fields or [])

                    if "title" not in locked and details.get("name"):
                        game.title = details["name"]

                    if "description" not in locked:
                        game.description = (
                            details.get("short_description") or ""
                        )

                    if "cover_image_url" not in locked:
                        game.cover_image_url = (
                            details.get("header_image") or ""
                        )

                    if "banner_image_url" not in locked:
                        game.banner_image_url = (
                            details.get("background") or ""
                        )

                    if "release_date" not in locked:
                        release = details.get("release_date") or {}
                        if not release.get("coming_soon"):
                            game.release_date = parse_release_date(
                                release.get("date", "")
                            )

                    game.save()

                    # Import genres.
                    if "genres" not in locked:
                        for item in details.get("genres") or []:
                            name = item.get("description", "").strip()
                            if name:
                                genre, _ = Genre.objects.get_or_create(
                                    name=name
                                )
                                game.genres.add(genre)

                    # Import supported platforms.
                    if "platforms" not in locked:
                        supported = details.get("platforms") or {}

                        platform_names = {
                            "windows": "Windows",
                            "mac": "macOS",
                            "linux": "Linux",
                        }

                        for key, name in platform_names.items():
                            if supported.get(key):
                                platform, _ = Platform.objects.get_or_create(
                                    name=name
                                )
                                game.platforms.add(platform)

                updated += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Imported {game.title} ({app_id})")
                )

            except (requests.RequestException, ValueError, TypeError) as exc:
                failed += 1
                self.stderr.write(f"Failed app {app_id}: {exc}")

            # Avoid sending requests too quickly to Steam.
            time.sleep(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Finished. Updated: {updated}, "
                f"Skipped: {skipped}, Failed: {failed}"
            )
        )