import re

from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Friendship, SecurityEvent, User, UserGameList, Game

STRONG = "Sh3lf-Quest-7731"


def make_user(username="mira", email="mira@example.com", password=STRONG, **extra):
    return User.objects.create_user(username=username, email=email, password=password, **extra)


class AuthFlowTests(APITestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache

    def test_register_creates_user_and_sends_verification_email(self):
        res = self.client.post(
            "/api/v1/auth/register/",
            {"username": "mira", "email": "Mira@Example.com", "password": STRONG},
        )
        self.assertEqual(res.status_code, 201)
        self.assertNotIn("password", res.data)
        user = User.objects.get(username="mira")
        self.assertEqual(user.email, "mira@example.com")  # stored lowercase
        self.assertIsNone(user.email_verified_at)
        self.assertNotEqual(user.password, STRONG)  # hashed
        self.assertEqual(len(mail.outbox), 1)

    def test_register_rejects_duplicates_and_weak_passwords(self):
        make_user()
        dup = self.client.post(
            "/api/v1/auth/register/",
            {"username": "MIRA", "email": "other@example.com", "password": STRONG},
        )
        self.assertEqual(dup.status_code, 400)
        self.assertIn("username", dup.data)

        dup_email = self.client.post(
            "/api/v1/auth/register/",
            {"username": "other", "email": "MIRA@example.com", "password": STRONG},
        )
        self.assertIn("email", dup_email.data)

        weak = self.client.post(
            "/api/v1/auth/register/",
            {"username": "other", "email": "other@example.com", "password": "12345678"},
        )
        self.assertEqual(weak.status_code, 400)
        self.assertIn("password", weak.data)

    def test_verify_email_works_once(self):
        self.client.post(
            "/api/v1/auth/register/",
            {"username": "mira", "email": "mira@example.com", "password": STRONG},
        )
        body = mail.outbox[0].body
        uid, token = re.search(r"uid=([^&\s]+)&token=([^\s]+)", body).groups()

        ok = self.client.post("/api/v1/auth/verify-email/", {"uid": uid, "token": token})
        self.assertEqual(ok.status_code, 200)
        self.assertIsNotNone(User.objects.get(username="mira").email_verified_at)

        replay = self.client.post("/api/v1/auth/verify-email/", {"uid": uid, "token": token})
        self.assertEqual(replay.status_code, 400)

    def test_login_logout_and_me(self):
        make_user()
        self.assertIn(self.client.get("/api/v1/users/me/").status_code, (401, 403))

        res = self.client.post("/api/v1/auth/login/", {"username": "mira", "password": STRONG})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["username"], "mira")

        me = self.client.get("/api/v1/users/me/")
        self.assertEqual(me.status_code, 200)
        self.assertFalse(me.data["email_verified"])

        self.assertEqual(self.client.post("/api/v1/auth/logout/").status_code, 204)
        self.assertIn(self.client.get("/api/v1/users/me/").status_code, (401, 403))

    def test_failed_login_is_logged_without_the_password(self):
        make_user()
        res = self.client.post("/api/v1/auth/login/", {"username": "mira", "password": "wrong"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("non_field_errors", res.data)
        event = SecurityEvent.objects.get()
        self.assertEqual(event.event_type, "login_failed")
        self.assertEqual(event.user.username, "mira")
        self.assertNotIn("wrong", str(event.details))

    def test_suspended_user_cannot_log_in(self):
        make_user(suspended_until=timezone.now() + timezone.timedelta(days=3))
        res = self.client.post("/api/v1/auth/login/", {"username": "mira", "password": STRONG})
        self.assertEqual(res.status_code, 403)

    def test_login_is_rate_limited(self):
        make_user()
        codes = [
            self.client.post("/api/v1/auth/login/", {"username": "mira", "password": "bad"}).status_code
            for _ in range(12)
        ]
        self.assertIn(429, codes)


class ProfileTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user()

    def test_patch_me_ignores_role_and_email(self):
        self.client.force_login(self.owner)
        res = self.client.patch(
            "/api/v1/users/me/",
            {"nickname": "Mira", "role": "admin", "email": "hack@example.com", "bio": "Hi"},
        )
        self.assertEqual(res.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.nickname, "Mira")
        self.assertEqual(self.owner.role, "user")
        self.assertEqual(self.owner.email, "mira@example.com")

    def test_public_profile_hides_private_data_and_never_shows_email(self):
        game = Game.objects.create(title="Hollow Knight")
        UserGameList.objects.create(user=self.owner, game=game, status="completed")
        self.owner.library_visibility = "private"
        self.owner.friends_visibility = "friends"
        self.owner.save()

        res = self.client.get("/api/v1/users/mira/")
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.data["library"])
        self.assertIsNone(res.data["friend_count"])
        self.assertNotIn("email", res.data)
        self.assertNotIn("role", res.data)

        # the owner sees their own
        self.client.force_login(self.owner)
        own = self.client.get("/api/v1/users/mira/")
        self.assertEqual(own.data["library"]["completed"], 1)

    def test_friends_can_see_friends_only_data(self):
        friend = make_user("kai", "kai@example.com")
        stranger = make_user("zed", "zed@example.com")
        Friendship.objects.create(user=friend, friend=self.owner, status="accepted")
        self.owner.friends_visibility = "friends"
        self.owner.save()

        self.client.force_login(friend)
        self.assertEqual(self.client.get("/api/v1/users/mira/").data["friend_count"], 1)
        self.client.force_login(stranger)
        self.assertIsNone(self.client.get("/api/v1/users/mira/").data["friend_count"])

    def test_inactive_user_profile_is_404(self):
        self.owner.is_active = False
        self.owner.save()
        self.assertEqual(self.client.get("/api/v1/users/mira/").status_code, 404)

    def test_change_password_keeps_session(self):
        self.client.force_login(self.owner)
        bad = self.client.post(
            "/api/v1/auth/password/change/",
            {"current_password": "nope", "new_password": "An0ther-Strong-Pass"},
        )
        self.assertEqual(bad.status_code, 400)

        ok = self.client.post(
            "/api/v1/auth/password/change/",
            {"current_password": STRONG, "new_password": "An0ther-Strong-Pass"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/users/me/").status_code, 200)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password("An0ther-Strong-Pass"))
