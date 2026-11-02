import numpy as np
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.core.models import (
    Game, GameLike, ModelRun, Rating, UserDismissedGame, UserGameList,
)
from apps.recommendation import algorithms, jobs
from apps.recommendation.algorithms import NotEnoughData


def block_data():
    """Users 1-5 love games 1-4 and dislike 5-8; users 6-10 are the reverse.
    User 1 never touched games 4 (liked by their block), 7 and 8 (disliked by it)."""
    data = []
    for u in range(1, 11):
        likes_first_block = u <= 5
        for g in range(1, 9):
            first = g <= 4
            high = 9 + (u + g) % 2
            low = 2 + (u + g) % 2
            data.append((u, g, high if first == likes_first_block else low))
    return [row for row in data if (row[0], row[1]) not in {(1, 4), (1, 7), (1, 8)}]


def seen_from(data):
    seen = defaultdict(set)
    for u, g, _ in data:
        seen[u].add(g)
    return seen


class InteractionValueTests(SimpleTestCase):
    def test_rating_beats_like_beats_status(self):
        self.assertEqual(algorithms.interaction_value(7, True, "dropped"), 7.0)
        self.assertEqual(algorithms.interaction_value(None, True, "dropped"), algorithms.LIKE_VALUE)
        self.assertEqual(algorithms.interaction_value(None, False, "completed"), 7.0)
        self.assertIsNone(algorithms.interaction_value(None, False, None))

    def test_dropped_is_not_a_positive(self):
        value = algorithms.interaction_value(None, False, "dropped")
        self.assertLess(value, algorithms.POSITIVE_VALUE)


class SvdAlgorithmTests(SimpleTestCase):
    def test_personalised_pick_comes_from_similar_players(self):
        data = block_data()
        model = algorithms.fit_svd(data, factors=2)

        recs = algorithms.recommend_svd(
            model,
            seen_from(data),
            {1: {1, 2, 3}},
            top_n=5,
        )

        # Temporary debug output
        print("SVD recommendations:", [
            (r.game_id, round(r.score, 3))
            for r in recs[1]
        ])

        ids = [r.game_id for r in recs[1]]
        self.assertEqual(ids[0], 4)

        for other_block_game in (7, 8):
            if other_block_game in ids:
                self.assertLess(ids.index(4), ids.index(other_block_game))

    def test_seen_games_are_never_recommended(self):
        data = block_data()
        seen = seen_from(data)
        model = algorithms.fit_svd(data, factors=2)
        for user_id, items in algorithms.recommend_svd(model, seen, {}).items():
            self.assertFalse({r.game_id for r in items} & seen[user_id])

    def test_scores_stay_on_the_rating_scale(self):
        data = block_data()
        model = algorithms.fit_svd(data, factors=2)
        for items in algorithms.recommend_svd(model, seen_from(data), {}).values():
            for r in items:
                self.assertTrue(1.0 <= r.score <= 10.0)

    def test_same_data_gives_same_model(self):
        a = algorithms.fit_svd(block_data(), factors=2)
        b = algorithms.fit_svd(block_data(), factors=2)
        self.assertTrue(np.allclose(a.user_factors @ a.item_factors, b.user_factors @ b.item_factors))

    def test_insufficient_data(self):
        with self.assertRaises(NotEnoughData):
            algorithms.fit_svd([])
        with self.assertRaises(NotEnoughData):
            algorithms.fit_svd([(1, 1, 9), (2, 2, 8)])

    def test_uniform_ratings_teach_nothing(self):
        data = [(u, g, 8) for u in range(1, 7) for g in range(1, 7)]
        with self.assertRaises(NotEnoughData):
            algorithms.fit_svd(data)

    def test_invalid_values_are_ignored(self):
        data = block_data() + [(1, 98, float("nan")), (1, 99, 0)]
        model = algorithms.fit_svd(data, factors=2)
        self.assertNotIn(98, model.game_ids)
        self.assertNotIn(99, model.game_ids)


class PlayersLikeYouAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.users = {
            i: User.objects.create_user(username=f"u{i}", email=f"u{i}@example.test", password="pw")
            for i in range(1, 11)
        }
        cls.games = {i: Game.objects.create(title=f"Game {i}", slug=f"game-{i}") for i in range(1, 9)}
        for u, g, score in block_data():
            Rating.objects.create(user=cls.users[u], game=cls.games[g], score=score)
        cls.model_run = jobs.build_recommendations(factors=2, top_n=5)

    def get(self, name, user=None):
        if user:
            self.client.force_login(user)
        return self.client.get(reverse(name))

    def test_job_succeeded_and_is_active(self):
        self.assertEqual(self.model_run.status, ModelRun.Status.SUCCEEDED)
        self.assertTrue(self.model_run.is_active)

    def test_personalised_results(self):
        body = self.get("players-like-you-list", self.users[1]).json()
        self.assertEqual(body["source"], "personalized")
        self.assertEqual(body["algorithm"], "svd")
        self.assertEqual(body["results"][0]["game"]["id"], self.games[4].id)

    def test_old_route_still_works_with_same_shape(self):
        body = self.get("recommendation-list", self.users[1]).json()
        self.assertEqual(body["source"], "personalized")
        self.assertIn("reason_text", body["results"][0])

    def test_games_added_after_the_run_are_hidden(self):
        GameLike.objects.create(user=self.users[1], game=self.games[4])
        body = self.get("players-like-you-list", self.users[1]).json()
        self.assertNotIn(self.games[4].id, [r["game"]["id"] for r in body["results"]])

    def test_dismissed_games_are_hidden(self):
        UserDismissedGame.objects.create(user=self.users[1], game=self.games[4])
        body = self.get("players-like-you-list", self.users[1]).json()
        self.assertNotIn(self.games[4].id, [r["game"]["id"] for r in body["results"]])

    def test_new_user_gets_labelled_popular_fallback(self):
        newbie = get_user_model().objects.create_user(username="new", email="new@example.test", password="pw")
        UserGameList.objects.create(user=newbie, game=self.games[1], status="plan_to_play")
        body = self.get("players-like-you-list", newbie).json()
        self.assertEqual(body["source"], "popular")
        self.assertEqual(body["algorithm"], "popularity")
        self.assertEqual(body["fallback_reason"], "not_enough_activity")
        self.assertNotIn(self.games[1].id, [r["game"]["id"] for r in body["results"]])

    def test_anonymous_users(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse("players-like-you-list")).status_code, (401, 403))
        body = self.client.get(reverse("recommendation-list")).json()
        self.assertEqual(body["source"], "popular")
        self.assertEqual(body["fallback_reason"], "login_required")


class NotEnoughDataAPITests(TestCase):
    def test_failed_run_keeps_the_page_working(self):
        user = get_user_model().objects.create_user(username="solo", email="s@example.test", password="pw")
        game = Game.objects.create(title="Only game", slug="only-game")
        Rating.objects.create(user=user, game=game, score=9)

        run = jobs.build_recommendations()
        self.assertEqual(run.status, ModelRun.Status.FAILED)
        self.assertTrue(run.error)

        self.client.force_login(user)
        body = self.client.get(reverse("players-like-you-list")).json()
        self.assertEqual(body["source"], "popular")
        self.assertEqual(body["fallback_reason"], "model_not_ready")