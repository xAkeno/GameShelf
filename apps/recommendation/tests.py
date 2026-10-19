from io import StringIO

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APITestCase

from apps.core.models import (
    Cluster,
    ClusterMember,
    Game,
    GameLike,
    GameScore,
    GameSimilarity,
    Genre,
    ModelRun,
    Rating,
    Tag,
    User,
    UserDismissedGame,
    UserRecommendation,
)

from . import algorithms, jobs


# ─── The algorithms on their own (no database) ────────────────────────────────
def taste_group_triples():
    """Games 1-4 are 'action', 5-8 are 'cozy'. Three action fans and three cozy fans each
    rate six games and leave one liked and one disliked game unrated."""
    action, cozy, triples = [1, 2, 3, 4], [5, 6, 7, 8], []
    for i in range(3):
        for g in action:
            if g != action[i]:
                triples.append((100 + i, g, 9.0))
        for g in cozy:
            if g != cozy[i]:
                triples.append((100 + i, g, 2.0))
        for g in cozy:
            if g != cozy[i]:
                triples.append((200 + i, g, 9.0))
        for g in action:
            if g != action[i]:
                triples.append((200 + i, g, 2.0))
    return triples


class AlgorithmTests(SimpleTestCase):
    def test_interaction_value_prefers_rating_then_like_then_status(self):
        self.assertEqual(algorithms.interaction_value(4, True, "completed"), 4.0)
        self.assertEqual(algorithms.interaction_value(None, True, "dropped"), algorithms.LIKE_VALUE)
        self.assertEqual(algorithms.interaction_value(None, False, "dropped"), 3.0)
        self.assertIsNone(algorithms.interaction_value(None, False, None))

    def test_bayesian_pulls_small_samples_toward_the_average(self):
        stats = {1: (10.0, 1), 2: (9.0, 200), 3: (5.0, 100)}
        result = algorithms.bayesian_weighted_rating(stats, min_votes=25)
        self.assertGreater(result.scores[2], result.scores[1])  # 200 ratings of 9 beat one 10
        self.assertLess(result.scores[1], 10.0)
        self.assertGreater(result.scores[1], result.global_mean)

    def test_bayesian_with_no_ratings(self):
        result = algorithms.bayesian_weighted_rating({})
        self.assertEqual(result.scores, {})
        self.assertIsNone(result.global_mean)

    def test_tfidf_cosine_finds_the_game_with_matching_genre_and_tags(self):
        docs = [
            algorithms.build_game_document(["Action"], ["Souls-like"], "dark brutal bosses"),
            algorithms.build_game_document(["Action"], ["Souls-like"], "punishing dark combat"),
            algorithms.build_game_document(["Simulation"], ["Farming"], "relaxing crops village"),
        ]
        model = algorithms.build_tfidf([10, 11, 12], docs)
        neighbours = algorithms.top_similar(model.matrix, k=2, min_score=-1.0)
        self.assertEqual(neighbours[0][0][0], 1)  # game 10's closest match is game 11
        self.assertGreater(neighbours[0][0][1], neighbours[0][1][1])
        self.assertTrue(all(0 <= score <= 1 for _, score in neighbours[0]))

    def test_tfidf_with_no_usable_text(self):
        with self.assertRaises(algorithms.NotEnoughData):
            algorithms.build_tfidf([1], [""])

    def test_svd_recommends_the_hidden_liked_game_first(self):
        triples = taste_group_triples()
        model = algorithms.fit_svd(triples, factors=2)
        seen, positives = {}, {}
        for user, game, value in triples:
            seen.setdefault(user, set()).add(game)
            if value >= algorithms.POSITIVE_VALUE:
                positives.setdefault(user, set()).add(game)
        recs = algorithms.recommend_svd(model, seen, positives, top_n=2)

        self.assertEqual(recs[100][0].game_id, 1)  # action fan: the action game they haven't rated
        self.assertEqual(recs[100][1].game_id, 5)
        self.assertEqual(recs[200][0].game_id, 5)  # cozy fan: the cozy game they haven't rated
        self.assertIn(recs[100][0].reason_game_id, {2, 3, 4})  # explained by a liked action game
        self.assertLess(model.train_rmse, 1.5)
        self.assertTrue(all(1.0 <= r.score <= 10.0 for r in recs[100]))

    def test_svd_needs_enough_data(self):
        with self.assertRaises(algorithms.NotEnoughData):
            algorithms.fit_svd([(1, 1, 9.0), (1, 2, 5.0)])

    def test_kmeans_separates_two_obvious_groups(self):
        rng = np.random.default_rng(0)
        points = np.vstack([rng.normal(0, 0.1, (10, 2)), rng.normal(5, 0.1, (10, 2))])
        result = algorithms.cluster_points(points)
        self.assertEqual(result.k, 2)
        self.assertEqual(len(set(result.labels[:10])), 1)
        self.assertNotEqual(result.labels[0], result.labels[-1])
        self.assertEqual(result.coords.shape, (20, 2))
        with self.assertRaises(algorithms.NotEnoughData):
            algorithms.cluster_points(points[:3])


# ─── The jobs and the API on a small world ────────────────────────────────────
def seed_world():
    action_genre, cozy_genre = Genre.objects.create(name="Action"), Genre.objects.create(name="Simulation")
    combat, boss = Tag.objects.create(name="Combat", slug="combat"), Tag.objects.create(name="Boss Rush", slug="boss-rush")
    farm, relax = Tag.objects.create(name="Farming", slug="farming"), Tag.objects.create(name="Relaxing", slug="relaxing")

    def make(title, genre, tags, description):
        game = Game.objects.create(title=title, slug=title.lower().replace(" ", "-"), description=description)
        game.genres.add(genre)
        game.tags.add(*tags)
        return game

    action = [
        make(t, action_genre, [combat, boss], "dark brutal bosses and punishing combat")
        for t in ("Blade Saga", "Iron Duel", "Shadow Rift", "Crimson Arena")
    ]
    cozy = [
        make(t, cozy_genre, [farm, relax], "relaxing farming crops and village life")
        for t in ("Pine Farm", "Tea Garden", "Moon Cafe", "Pond Life")
    ]
    unrated = Game.objects.create(title="Unrated Mystery", slug="unrated-mystery", description="a quiet mystery")

    fans_action = [User.objects.create(username=f"action{i}", email=f"action{i}@x.com") for i in range(3)]
    fans_cozy = [User.objects.create(username=f"cozy{i}", email=f"cozy{i}@x.com") for i in range(3)]
    for i in range(3):
        for g in action:
            if g != action[i]:
                Rating.objects.create(user=fans_action[i], game=g, score=9)
        for g in cozy:
            if g != cozy[i]:
                Rating.objects.create(user=fans_action[i], game=g, score=2)
        for g in cozy:
            if g != cozy[i]:
                Rating.objects.create(user=fans_cozy[i], game=g, score=9)
        for g in action:
            if g != action[i]:
                Rating.objects.create(user=fans_cozy[i], game=g, score=2)
    return {"action": action, "cozy": cozy, "unrated": unrated, "fans_action": fans_action, "fans_cozy": fans_cozy}


class JobTests(TestCase):
    def setUp(self):
        self.world = seed_world()

    def test_bayesian_job_writes_scores_for_every_game(self):
        run = jobs.build_bayesian_scores()
        self.assertEqual(run.status, "succeeded")
        self.assertTrue(run.is_active)
        self.assertEqual(GameScore.objects.count(), Game.objects.count())
        rated = GameScore.objects.get(game=self.world["action"][0])
        self.assertEqual(rated.rating_count, 4)
        self.assertEqual(float(rated.average_rating), 5.5)
        self.assertIsNotNone(rated.bayesian_score)
        self.assertIsNone(GameScore.objects.get(game=self.world["unrated"]).bayesian_score)

    def test_similarity_job_links_games_with_shared_genres(self):
        run = jobs.build_similarities(top_n=3)
        self.assertEqual(run.status, "succeeded")
        first = GameSimilarity.objects.filter(model_run=run, game=self.world["action"][0]).order_by("rank").first()
        self.assertIn(first.similar_game_id, [g.id for g in self.world["action"]])
        self.assertEqual(first.reason["genres"], ["Action"])
        self.assertFalse(GameSimilarity.objects.filter(game_id=first.game_id, similar_game_id=first.game_id).exists())

    def test_svd_job_recommends_unseen_liked_game_with_a_real_reason(self):
        run = jobs.build_recommendations(factors=2, top_n=3)
        self.assertEqual(run.status, "succeeded", run.error)
        fan = self.world["fans_action"][0]
        picks = list(UserRecommendation.objects.filter(model_run=run, user=fan).order_by("rank"))
        self.assertEqual(picks[0].game_id, self.world["action"][0].id)
        self.assertTrue(picks[0].reason_text.startswith("Because you"))
        rated = set(Rating.objects.filter(user=fan).values_list("game_id", flat=True))
        self.assertFalse(any(p.game_id in rated for p in picks))  # never suggests what they already rated

    def test_kmeans_job_clusters_games_and_players(self):
        run = jobs.build_clusters()
        self.assertEqual(run.status, "succeeded", run.error)
        self.assertTrue(Cluster.objects.filter(model_run=run, entity_type="game").exists())
        self.assertTrue(Cluster.objects.filter(model_run=run, entity_type="user").exists())
        self.assertEqual(ClusterMember.objects.filter(cluster__model_run=run, game__isnull=False).count(), Game.objects.count())
        self.assertTrue(all(c.label for c in Cluster.objects.filter(model_run=run)))

    def test_management_command_runs_all_four_jobs(self):
        out = StringIO()
        call_command("build_recommendations", "--factors", "2", stdout=out)
        text = out.getvalue()
        for name in ("bayesian", "tfidf", "svd", "kmeans"):
            self.assertIn(f"{name}: done", text)
        only = StringIO()
        call_command("build_recommendations", "--only", "bayesian", stdout=only)
        self.assertNotIn("svd", only.getvalue())

    def test_only_one_active_run_and_old_runs_are_pruned(self):
        for _ in range(3):
            jobs.build_bayesian_scores()
        self.assertEqual(ModelRun.objects.filter(algorithm="bayesian", is_active=True).count(), 1)
        self.assertEqual(ModelRun.objects.filter(algorithm="bayesian").count(), jobs.KEEP_RUNS)


class NotEnoughDataTests(TestCase):
    def test_failed_run_is_recorded_and_nothing_goes_live(self):
        game = Game.objects.create(title="Solo")
        user = User.objects.create(username="solo", email="solo@x.com")
        Rating.objects.create(user=user, game=game, score=8)
        run = jobs.build_recommendations()
        self.assertEqual(run.status, "failed")
        self.assertIn("Need at least", run.error)
        self.assertFalse(run.is_active)
        self.assertEqual(UserRecommendation.objects.count(), 0)


class ApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.world = seed_world()
        for fan in self.world["fans_action"][:2]:
            GameLike.objects.create(user=fan, game=self.world["action"][0])
        jobs.build_bayesian_scores()
        jobs.build_similarities(top_n=3)
        jobs.build_recommendations(factors=2, top_n=3)
        jobs.build_clusters()

    def test_visitors_and_new_players_get_popular_games_not_fake_history(self):
        res = self.client.get("/api/v1/recommendations/")
        self.assertEqual(res.data["source"], "popular")
        self.assertTrue(res.data["results"])
        self.assertEqual(res.data["results"][0]["reason_type"], "popular")

        newcomer = User.objects.create(username="newbie", email="newbie@x.com")
        self.client.force_login(newcomer)
        self.assertEqual(self.client.get("/api/v1/recommendations/").data["source"], "popular")

    def test_logged_in_player_gets_personalised_picks_with_an_explanation(self):
        # the third action fan has no likes and left action[2] unrated, so that is their top pick
        self.client.force_login(self.world["fans_action"][2])
        res = self.client.get("/api/v1/recommendations/")
        self.assertEqual(res.data["source"], "personalized")
        first = res.data["results"][0]
        self.assertEqual(first["game"]["id"], self.world["action"][2].id)
        self.assertTrue(first["reason_text"].startswith("Because you"))
        self.assertIsNotNone(first["reason_game"])

    def test_dismissed_games_are_filtered_at_read_time(self):
        fan, target = self.world["fans_action"][2], self.world["action"][2]
        self.client.force_login(fan)
        before = [r["game"]["id"] for r in self.client.get("/api/v1/recommendations/").data["results"]]
        self.assertIn(target.id, before)
        UserDismissedGame.objects.create(user=fan, game=target)
        after = [r["game"]["id"] for r in self.client.get("/api/v1/recommendations/").data["results"]]
        self.assertNotIn(target.id, after)

    def test_similar_games_endpoint(self):
        game = self.world["action"][0]
        res = self.client.get(f"/api/v1/similar/{game.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["results"])
        self.assertIn(res.data["results"][0]["game"]["id"], [g.id for g in self.world["action"]])
        self.assertEqual(self.client.get("/api/v1/similar/999999/").status_code, 404)

    def test_top_games_orderings(self):
        popular = self.client.get("/api/v1/top-games/?ordering=popularity&limit=3")
        self.assertEqual(popular.data["results"][0]["game"]["id"], self.world["action"][0].id)  # it has two likes
        rated = self.client.get("/api/v1/top-games/?ordering=rating&limit=50")
        ids = [r["game"]["id"] for r in rated.data["results"]]
        self.assertNotIn(self.world["unrated"].id, ids)
        self.assertIsInstance(rated.data["results"][0]["bayesian_score"], float)

    def test_clusters_endpoints(self):
        listing = self.client.get("/api/v1/clusters/")
        self.assertTrue(listing.data["results"])
        cluster_id = listing.data["results"][0]["id"]
        detail = self.client.get(f"/api/v1/clusters/{cluster_id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["games"])
        self.assertEqual(self.client.get("/api/v1/clusters/999999/").status_code, 404)
