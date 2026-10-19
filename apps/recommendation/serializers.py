from rest_framework import serializers

from apps.core.models import ClusterMember, Cluster, Game, GameScore, GameSimilarity, UserRecommendation


class GameBriefSerializer(serializers.ModelSerializer):
    genres = serializers.SlugRelatedField(many=True, read_only=True, slug_field="name")

    class Meta:
        model = Game
        fields = ["id", "title", "slug", "cover_image_url", "release_date", "genres"]


class GameMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Game
        fields = ["id", "title", "slug"]


class RecommendationSerializer(serializers.ModelSerializer):
    game = GameBriefSerializer(read_only=True)
    reason_game = GameMiniSerializer(read_only=True, allow_null=True)

    class Meta:
        model = UserRecommendation
        fields = ["game", "score", "rank", "reason_type", "reason_text", "reason_game"]


class SimilarGameSerializer(serializers.ModelSerializer):
    game = GameBriefSerializer(source="similar_game", read_only=True)

    class Meta:
        model = GameSimilarity
        fields = ["game", "score", "rank", "reason"]


class GameScoreSerializer(serializers.ModelSerializer):
    game = GameBriefSerializer(read_only=True)
    average_rating = serializers.FloatField(allow_null=True)
    bayesian_score = serializers.FloatField(allow_null=True)

    class Meta:
        model = GameScore
        fields = [
            "game", "average_rating", "rating_count", "bayesian_score",
            "popularity_score", "trending_score",
        ]


class ClusterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cluster
        fields = ["id", "label", "size"]


class ClusterMemberSerializer(serializers.ModelSerializer):
    game = GameBriefSerializer(read_only=True)

    class Meta:
        model = ClusterMember
        fields = ["game", "distance"]
