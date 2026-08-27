"""Sayfa yenilendiğinde oturumun korunması ve cihaz baypasının sınırı.

Masada oturan müşteri sayfayı yenilediğinde "Erişim Reddedildi" duvarına
çarpıyordu. Sebep: sayfa açılışı, elde 90 dakikalık geçerli bir oturum olsa
bile URL'deki 30 saniyelik QR kodunu yeniden doğrulatmaya çalışıyordu. Sipariş
vermiş müşteriler bunu fark etmiyordu çünkü sunucudaki cihaz baypası onları
kurtarıyordu — ve o baypasın hiçbir zaman sınırı yoktu.

Bu dosya iki şeyi birlikte tutar:

1. `GET /api/auth/musteri/oturum` yalnızca gerçekten geçerli bir oturumu kabul
   eder. İstemcinin `localStorage`'a yazacağı uydurma bir değer kapıyı açmaz;
   karar sunucunundur.
2. Cihaz baypası artık `DEVICE_PRESENCE_GRACE` ile sınırlıdır. Fiziksel varlık
   kanıtı, hangi yoldan gelirse gelsin aynı süre yaşar.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.auth.dependencies import get_current_customer
from app.services.auth_service import CUSTOMER_SESSION_TTL_MINUTES
from app.services.masa_service import DEVICE_PRESENCE_GRACE, MasaService


class _Credentials:
    def __init__(self, token, scheme="Bearer"):
        self.credentials = token
        self.scheme = scheme


class CustomerSessionEndpointTests(unittest.TestCase):
    """`get_current_customer`, sipariş yollarının kullandığı doğrulamanın aynısı."""

    def _repo_returning(self, session):
        repo = MagicMock()
        repo.get_active_customer_session.return_value = session
        return repo

    def test_a_live_session_is_accepted(self):
        session = {
            "id": 184,
            "masa_id": 44,
            "device_id": "dev-a",
            "expires_at": datetime.now() + timedelta(minutes=CUSTOMER_SESSION_TTL_MINUTES),
        }
        result = get_current_customer(_Credentials("a" * 64), self._repo_returning(session))
        self.assertEqual(result["masa_id"], 44)

    def test_an_unknown_token_is_rejected(self):
        """Uydurulmuş bir localStorage değeri kapıyı açmamalı."""
        with self.assertRaises(HTTPException) as ctx:
            get_current_customer(_Credentials("sahte-token"), self._repo_returning(None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_a_missing_token_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            get_current_customer(None, self._repo_returning(None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_a_revoked_session_is_rejected(self):
        """Adisyon kapanınca oturumlar iptal edilir; sorgu onları döndürmez."""
        with self.assertRaises(HTTPException) as ctx:
            get_current_customer(_Credentials("b" * 64), self._repo_returning(None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_the_endpoint_exposes_only_the_table_id(self):
        """Oturum kimliği ve cihaz kimliği yanıta sızmamalı."""
        from app.schemas.auth import MusteriOturumResponse

        self.assertEqual(
            set(MusteriOturumResponse.model_fields),
            {"valid", "masa_id"},
        )

    def test_the_route_is_registered(self):
        # `app.routes` bu FastAPI sürümünde alt router'ları `_IncludedRouter`
        # sarmalayıcısı olarak tutar, yani iç yolları göstermez. OpenAPI şeması
        # uygulamanın gerçekten sunduğu yolların yetkili listesidir.
        from app.main import app

        paths = app.openapi()["paths"]
        self.assertIn("/api/auth/musteri/oturum", paths)
        self.assertIn("get", paths["/api/auth/musteri/oturum"])


class CustomerSessionEndpointEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Uç noktayı gerçek ASGI uygulaması üzerinden, tam dependency zinciriyle sürer."""

    LIVE_TOKEN = "c" * 64
    TABLE_ID = 44

    def _install_fake_repo(self):
        import hashlib

        from app.auth.dependencies import get_current_customer as dependency
        from app.main import app
        from app.repositories.auth_repo import AuthRepository

        expected_hash = hashlib.sha256(self.LIVE_TOKEN.encode("utf-8")).hexdigest()
        table_id = self.TABLE_ID

        class _Repo:
            def get_active_customer_session(self, session_token_hash):
                if session_token_hash != expected_hash:
                    return None
                return {
                    "id": 184,
                    "masa_id": table_id,
                    "device_id": "dev-a",
                    "expires_at": "2099-01-01",
                }

            def touch_customer_session(self, *args, **kwargs):
                pass

        app.dependency_overrides[AuthRepository] = lambda: _Repo()
        self.addCleanup(app.dependency_overrides.clear)
        return app, dependency

    async def _get(self, app, token):
        from tests.test_customer_session_authorization import asgi_request

        return await asgi_request(app, "GET", "/api/auth/musteri/oturum", token=token)

    async def test_a_live_token_returns_its_table(self):
        app, _ = self._install_fake_repo()
        status, body = await self._get(app, self.LIVE_TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["masa_id"], self.TABLE_ID)
        self.assertTrue(body["valid"])

    async def test_a_forged_localstorage_value_is_refused(self):
        """`localStorage`'a rastgele bir şey yazmak kapıyı açmamalı."""
        app, _ = self._install_fake_repo()
        status, _body = await self._get(app, "uydurma-token")
        self.assertEqual(status, 401)

    async def test_no_token_is_refused(self):
        app, _ = self._install_fake_repo()
        status, _body = await self._get(app, None)
        self.assertEqual(status, 401)


class DeviceBypassWindowTests(unittest.TestCase):
    """Cihaz baypası: "açık siparişi var" değil, "yakın zamanda sipariş vermiş"."""

    DEVICE = "dev-onzksbp5i-1786358284974"
    NOW = datetime(2026, 8, 24, 20, 0, 0)

    def _order(self, *, device_id, minutes_ago):
        return {
            "id": 283,
            "device_id": device_id,
            "olusturma_tarihi": self.NOW - timedelta(minutes=minutes_ago),
        }

    def test_a_recent_order_still_proves_presence(self):
        orders = [self._order(device_id=self.DEVICE, minutes_ago=10)]
        self.assertTrue(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_an_order_at_the_edge_of_the_window_is_accepted(self):
        minutes = int(DEVICE_PRESENCE_GRACE.total_seconds() // 60)
        orders = [self._order(device_id=self.DEVICE, minutes_ago=minutes)]
        self.assertTrue(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_a_stale_order_no_longer_opens_the_door(self):
        """Kapatılmayı unutulmuş bir adisyon süresiz giriş hakkı vermemeli."""
        minutes = int(DEVICE_PRESENCE_GRACE.total_seconds() // 60) + 1
        orders = [self._order(device_id=self.DEVICE, minutes_ago=minutes)]
        self.assertFalse(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_a_days_old_order_is_firmly_rejected(self):
        orders = [self._order(device_id=self.DEVICE, minutes_ago=3 * 24 * 60)]
        self.assertFalse(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_another_devices_recent_order_proves_nothing(self):
        orders = [self._order(device_id="baska-cihaz", minutes_ago=1)]
        self.assertFalse(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_the_newest_matching_order_decides(self):
        """Eski bir sipariş, yakın tarihli bir siparişi geçersiz kılmamalı."""
        orders = [
            self._order(device_id=self.DEVICE, minutes_ago=5000),
            self._order(device_id=self.DEVICE, minutes_ago=5),
        ]
        self.assertTrue(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_an_unreadable_timestamp_fails_towards_the_qr_code(self):
        """Zaman okunamıyorsa kanıt sayılmaz; müşteri QR'ı okutur."""
        orders = [{"device_id": self.DEVICE, "olusturma_tarihi": "2026-08-24 19:55:00"}]
        self.assertFalse(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_a_missing_timestamp_fails_towards_the_qr_code(self):
        orders = [{"device_id": self.DEVICE}]
        self.assertFalse(
            MasaService._device_has_recent_order(orders, self.DEVICE, now=self.NOW)
        )

    def test_no_device_id_never_bypasses(self):
        orders = [self._order(device_id=None, minutes_ago=1)]
        self.assertFalse(MasaService._device_has_recent_order(orders, None, now=self.NOW))
        self.assertFalse(MasaService._device_has_recent_order(orders, "", now=self.NOW))

    def test_an_empty_table_never_bypasses(self):
        self.assertFalse(MasaService._device_has_recent_order([], self.DEVICE, now=self.NOW))

    def test_the_grace_matches_the_session_lifetime(self):
        """İki yol da aynı fiziksel varlık kanıtı; ömürleri ayrışmamalı."""
        self.assertEqual(
            DEVICE_PRESENCE_GRACE,
            timedelta(minutes=CUSTOMER_SESSION_TTL_MINUTES),
        )


if __name__ == "__main__":
    unittest.main()
