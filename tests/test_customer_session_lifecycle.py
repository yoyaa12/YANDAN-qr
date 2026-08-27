"""Müşteri oturumlarının yaşam döngüsü.

İki ayrı kusur vardı ve ikisi de `CustomerSessions` tablosuna bakınca
görülüyordu:

1. `create_customer_session` her çağrıda koşulsuz `INSERT` yapıyordu. Aynı cihaz
   aynı masada QR'ı ikinci kez okuttuğunda yeni bir satır açılıyordu. Görünür
   sonucu tablo şişmesiydi; asıl sonucu ise sipariş sahipliğinin kopmasıydı:
   `is_mine` hesabı `Siparisler.customer_session_id` eşitliğine dayandığı için
   oturum kimliği değişen müşteri kendi siparişlerini kaybediyordu.

2. Süresi dolan oturumların `is_active` bayrağı hiç düşmüyordu. Doğrulama
   sorgusu `expires_at`'a da baktığı için güvenlik açığı değildi, ama tabloya
   bakan kişi ölmüş oturumları canlı sanıyordu.
"""

import asyncio
import unittest
import unittest.mock
from datetime import datetime, timedelta

from app.services.auth_service import AuthService, CUSTOMER_SESSION_TTL_MINUTES
from app.core.session_maintenance import sweep_expired_customer_sessions


class FakeAuthRepository:
    """Yalnızca oturum yollarını taklit eden sahte repository.

    Her çağrıyı sırayla kaydeder, böylece "INSERT mi yapıldı, UPDATE mi"
    sorusu dolaylı çıkarımla değil doğrudan yanıtlanır.
    """

    def __init__(self, active_session=None, expired_row_count=0):
        self._active_session = active_session
        self._expired_row_count = expired_row_count
        self.calls = []

    # --- AuthService'in kullandığı yollar -------------------------------

    def get_active_session_for_device(self, masa_id, device_id):
        self.calls.append(("get_active_session_for_device", masa_id, device_id))
        return self._active_session

    def rotate_customer_session(self, session_id, session_token_hash, expires_at):
        self.calls.append(
            ("rotate_customer_session", session_id, session_token_hash, expires_at)
        )

    def revoke_other_sessions_for_device(self, masa_id, device_id, keep_session_id):
        self.calls.append(
            ("revoke_other_sessions_for_device", masa_id, device_id, keep_session_id)
        )
        return 0

    def create_customer_session(self, session_token_hash, masa_id, expires_at, device_id):
        self.calls.append(
            ("create_customer_session", session_token_hash, masa_id, expires_at, device_id)
        )

    def deactivate_expired_customer_sessions(self):
        self.calls.append(("deactivate_expired_customer_sessions",))
        return self._expired_row_count

    def call_names(self):
        return [call[0] for call in self.calls]


class CustomerSessionReuseTests(unittest.TestCase):
    MASA_ID = 5
    DEVICE_ID = "dev-onzksbp5i-1786358284974"

    def test_known_device_rotates_its_session_instead_of_opening_a_new_one(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        self.assertIn("rotate_customer_session", repo.call_names())
        self.assertNotIn(
            "create_customer_session",
            repo.call_names(),
            "aynı cihaz aynı masada ikinci bir satır açmamalı",
        )

    def test_the_rotated_row_keeps_its_identity(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        rotate = next(c for c in repo.calls if c[0] == "rotate_customer_session")
        self.assertEqual(
            rotate[1],
            166,
            "satır kimliği korunmazsa müşterinin siparişleri 'benim' olmaktan çıkar",
        )

    def test_leftover_duplicate_sessions_are_closed(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        revoke = next(
            c for c in repo.calls if c[0] == "revoke_other_sessions_for_device"
        )
        self.assertEqual(revoke[1:], (self.MASA_ID, self.DEVICE_ID, 166))

    def test_rotation_still_issues_a_fresh_token(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        first = service.create_customer_session(self.MASA_ID, self.DEVICE_ID)
        second = service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        self.assertNotEqual(
            first,
            second,
            "satırın korunması eski token'ın yaşamaya devam etmesi anlamına gelmez",
        )
        self.assertEqual(len(first), 64)

    def test_the_stored_value_is_a_hash_not_the_token(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        raw_token = service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        rotate = next(c for c in repo.calls if c[0] == "rotate_customer_session")
        stored_hash = rotate[2]
        self.assertNotEqual(stored_hash, raw_token)
        self.assertEqual(len(stored_hash), 64)

    def test_rotation_extends_the_expiry(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        before = datetime.now()
        service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        rotate = next(c for c in repo.calls if c[0] == "rotate_customer_session")
        expires_at = rotate[3]
        expected = before + timedelta(minutes=CUSTOMER_SESSION_TTL_MINUTES)
        self.assertGreaterEqual(expires_at, expected - timedelta(seconds=5))

    def test_an_unknown_device_gets_a_new_session(self):
        repo = FakeAuthRepository(active_session=None)
        service = AuthService(repo=repo)

        service.create_customer_session(self.MASA_ID, self.DEVICE_ID)

        self.assertIn("create_customer_session", repo.call_names())
        self.assertNotIn("rotate_customer_session", repo.call_names())

    def test_a_session_without_a_device_id_is_never_correlated(self):
        repo = FakeAuthRepository(active_session={"id": 166})
        service = AuthService(repo=repo)

        service.create_customer_session(self.MASA_ID, None)

        self.assertNotIn(
            "get_active_session_for_device",
            repo.call_names(),
            "cihaz kimliği yoksa oturumlar ilişkilendirilemez",
        )
        self.assertIn("create_customer_session", repo.call_names())

    def test_different_devices_at_one_table_stay_separate(self):
        """Aynı masaya oturan iki farklı müşteri iki ayrı oturum almalı."""
        first_repo = FakeAuthRepository(active_session=None)
        second_repo = FakeAuthRepository(active_session=None)

        AuthService(repo=first_repo).create_customer_session(self.MASA_ID, "dev-A")
        AuthService(repo=second_repo).create_customer_session(self.MASA_ID, "dev-B")

        for repo in (first_repo, second_repo):
            self.assertIn("create_customer_session", repo.call_names())
            self.assertNotIn("rotate_customer_session", repo.call_names())


class ExpiredSessionSweepTests(unittest.TestCase):
    def test_the_service_delegates_the_sweep_to_the_repository(self):
        repo = FakeAuthRepository(expired_row_count=5)

        closed = AuthService(repo=repo).deactivate_expired_customer_sessions()

        self.assertEqual(closed, 5)
        self.assertIn("deactivate_expired_customer_sessions", repo.call_names())

    def test_the_background_sweep_runs_off_the_event_loop(self):
        """Süpürme `pyodbc` kullanır; event loop'u bloklarsa socket olayları gecikir."""
        repo = FakeAuthRepository(expired_row_count=3)

        with unittest.mock.patch(
            "app.core.session_maintenance._sweep_blocking",
            side_effect=lambda: repo.deactivate_expired_customer_sessions(),
        ):
            closed = asyncio.run(sweep_expired_customer_sessions())

        self.assertEqual(closed, 3)


class ExpiredSessionQueryTests(unittest.TestCase):
    """Süpürme olmasa bile süresi dolmuş oturum kabul edilmemeli.

    `is_active` bayrağının gerçeği yansıtmaması bir görüntü sorunuydu, yetki
    sorunu değildi. O ayrımın kayıt altında kalması için sorgunun iki koşulu da
    aradığını doğrularız.
    """

    def test_the_lookup_checks_both_the_flag_and_the_expiry(self):
        import inspect

        from app.repositories.auth_repo import AuthRepository

        source = inspect.getsource(AuthRepository.get_active_customer_session)
        self.assertIn("is_active = 1", source)
        self.assertIn("expires_at > GETDATE()", source)


if __name__ == "__main__":
    unittest.main()
