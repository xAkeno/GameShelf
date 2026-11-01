from django.apps import AppConfig


class RecommendationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recommendation"

    def ready(self):
        import threading

        def warm():
            try:
                from . import views

                views.steam_games()
                views.load_recommendation_artifact("tfidf_similarities.json")
            except Exception:
                pass

        threading.Thread(target=warm, daemon=True).start()