"""Tests for AGENTS.md §14: the BOS -> DOLU physical-presence rule.

This rule is the project's critical anti-troll business rule: whoever claims an
empty table with the first order must prove they are physically at the table by
supplying the currently valid 6-digit dynamic code.

Before this file neither `SiparisService.create_siparis` nor
`app/core/totp_service.py` had any automated coverage, so the rule was
"implemented" but never demonstrated to work — and nothing would have failed if
the check were deleted.
"""

import asyncio
import contextlib
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.core import totp_service
from app.core.totp_service import (
    generate_dynamic_token,
    generate_secret_key,
    verify_dynamic_token,
)
from app.enums import PaymentMethod, TableStatus
from app.schemas.orders import SiparisItemModel, SiparisOlusturModel
from app.services import siparis_service as siparis_service_module
from app.services.siparis_service import SiparisService


@contextlib.contextmanager
def _no_transaction():
    yield None


class DynamicTokenServiceTests(unittest.TestCase):
    """The token itself: time window, tolerance and replay protection."""

    def setUp(self):
        totp_service._used_tokens.clear()
        self.addCleanup(totp_service._used_tokens.clear)
        self.secret = generate_secret_key()
        # Fixed instant so the window arithmetic is deterministic.
        self.now = 1_800_000_000.0

    def test_secret_is_32_hex_characters(self):
        self.assertEqual(len(self.secret), 32)
        int(self.secret, 16)  # raises if not hex

    def test_token_is_six_digits_and_verifies(self):
        token = generate_dynamic_token(self.secret, self.now)
        self.assertEqual(len(token), 6)
        self.assertTrue(token.isdigit())
        self.assertTrue(
            verify_dynamic_token(1, self.secret, token, self.now, mark_as_used=False)
        )

    def test_another_tables_secret_does_not_validate(self):
        token = generate_dynamic_token(self.secret, self.now)
        other_secret = generate_secret_key()
        self.assertFalse(
            verify_dynamic_token(1, other_secret, token, self.now, mark_as_used=False)
        )

    def test_tolerance_accepts_two_windows_in_each_direction(self):
        for offset in (-60, -30, 0, 30, 60):
            with self.subTest(offset=offset):
                token = generate_dynamic_token(self.secret, self.now + offset)
                self.assertTrue(
                    verify_dynamic_token(
                        1, self.secret, token, self.now, mark_as_used=False
                    ),
                    f"{offset}s kaymasi kabul edilmeliydi",
                )

    def test_tolerance_stops_at_three_windows(self):
        for offset in (-120, 120):
            with self.subTest(offset=offset):
                token = generate_dynamic_token(self.secret, self.now + offset)
                self.assertFalse(
                    verify_dynamic_token(
                        1, self.secret, token, self.now, mark_as_used=False
                    ),
                    f"{offset}s kaymasi reddedilmeliydi",
                )

    def test_a_consumed_token_cannot_be_replayed(self):
        token = generate_dynamic_token(self.secret, self.now)
        self.assertTrue(
            verify_dynamic_token(1, self.secret, token, self.now, mark_as_used=True)
        )
        self.assertFalse(
            verify_dynamic_token(1, self.secret, token, self.now, mark_as_used=True),
            "Ayni kod ikinci kez kabul edilmemeli (replay)",
        )

    def test_consuming_one_table_token_does_not_burn_another_table(self):
        token = generate_dynamic_token(self.secret, self.now)
        verify_dynamic_token(1, self.secret, token, self.now, mark_as_used=True)
        self.assertTrue(
            verify_dynamic_token(2, self.secret, token, self.now, mark_as_used=True)
        )

    def test_empty_secret_or_token_is_rejected(self):
        self.assertFalse(verify_dynamic_token(1, "", "123456", self.now))
        self.assertFalse(verify_dynamic_token(1, self.secret, "", self.now))


class FirstOrderPhysicalVerificationTests(unittest.IsolatedAsyncioTestCase):
    """`create_siparis` end to end, with the real TOTP verifier."""

    def setUp(self):
        totp_service._used_tokens.clear()
        self.addCleanup(totp_service._used_tokens.clear)
        siparis_service_module._RECENT_ORDERS_CACHE.clear()
        self.addCleanup(siparis_service_module._RECENT_ORDERS_CACHE.clear)
        siparis_service_module.TABLE_MOVES_MAP.clear()
        self.addCleanup(siparis_service_module.TABLE_MOVES_MAP.clear)

        self.secret = generate_secret_key()
        self.masa_id = 5

        self.mock_siparis_repo = MagicMock()
        self.mock_masa_repo = MagicMock()
        self.mock_urun_repo = MagicMock()
        self.mock_auth_repo = MagicMock()

        self.mock_siparis_repo.create_siparis.return_value = 4242
        self.mock_auth_repo.get_banned_device.return_value = None
        self.mock_urun_repo.get_by_id.return_value = {
            "id": 1,
            "urun_adi": "Köfte",
            "fiyat": 100.0,
            "stok_miktari": 50,
            "aktif_mi": True,
        }
        self._set_table_status(TableStatus.EMPTY.value)

        self.service = SiparisService(
            siparis_repo=self.mock_siparis_repo,
            masa_repo=self.mock_masa_repo,
            urun_repo=self.mock_urun_repo,
            auth_repo=self.mock_auth_repo,
        )

        patcher_tx = patch("app.services.siparis_service.db_transaction", _no_transaction)
        patcher_bus = patch("app.services.siparis_service.event_bus")
        patcher_browsing = patch("app.services.siparis_service.clear_browsing_table")
        for patcher in (patcher_tx, patcher_bus, patcher_browsing):
            self.addCleanup(patcher.stop)
        patcher_tx.start()
        patcher_bus.start().publish = AsyncMock()
        patcher_browsing.start()

    def _set_table_status(self, durum: str):
        self.mock_masa_repo.get_by_id.return_value = {
            "id": self.masa_id,
            "masa_no": f"Masa {self.masa_id}",
            "durum": durum,
            "totp_secret": self.secret,
        }

    def _order(self, *, token=None, device_id="device-1", adet=1):
        return SiparisOlusturModel(
            masa_id=self.masa_id,
            toplam_tutar=100.0,
            odeme_yontemi=PaymentMethod.POS,
            urunler=[
                SiparisItemModel(urun_id=1, adet=adet, birim_fiyat=100.0, urun_notu="")
            ],
            device_id=device_id,
            current_totp_token=token,
        )

    # --- BOS masa: fiziksel dogrulama zorunlu ---------------------------------

    async def test_empty_table_rejects_an_order_without_a_code(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(self._order())

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("6 haneli", ctx.exception.detail)
        self.mock_siparis_repo.create_siparis.assert_not_called()
        self.mock_masa_repo.update_durum.assert_not_called()

    async def test_empty_table_rejects_a_wrong_code(self):
        wrong = generate_dynamic_token(generate_secret_key())
        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(self._order(token=wrong))

        self.assertEqual(ctx.exception.status_code, 403)
        self.mock_siparis_repo.create_siparis.assert_not_called()

    async def test_empty_table_rejects_a_code_from_three_windows_ago(self):
        stale = generate_dynamic_token(self.secret, time.time() - 120)
        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(self._order(token=stale))

        self.assertEqual(ctx.exception.status_code, 403)
        self.mock_siparis_repo.create_siparis.assert_not_called()

    async def test_valid_code_creates_the_order_and_occupies_the_table(self):
        token = generate_dynamic_token(self.secret)
        order = await self.service.create_siparis(self._order(token=token))

        self.assertEqual(order.id, 4242)
        self.assertEqual(order.masa_id, self.masa_id)
        self.mock_siparis_repo.create_siparis.assert_called_once()
        self.mock_masa_repo.update_durum.assert_called_once_with(
            self.masa_id, TableStatus.OCCUPIED.value
        )

    async def test_the_first_order_code_cannot_be_reused_by_a_second_device(self):
        """The troll scenario: a captured code must work exactly once."""
        token = generate_dynamic_token(self.secret)
        await self.service.create_siparis(self._order(token=token, device_id="device-1"))

        # Table would normally be DOLU now; keep it BOS so the code path under
        # test is the code check itself rather than the occupied-table branch.
        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(
                self._order(token=token, device_id="device-2")
            )
        self.assertEqual(ctx.exception.status_code, 403)

    # --- DOLU masa: arkadas katilimi -----------------------------------------

    async def test_occupied_table_accepts_an_order_without_a_code(self):
        self._set_table_status(TableStatus.OCCUPIED.value)
        order = await self.service.create_siparis(self._order())

        self.assertEqual(order.id, 4242)
        self.mock_siparis_repo.create_siparis.assert_called_once()

    # --- Diger create_siparis korumalari --------------------------------------

    async def test_banned_device_is_rejected_before_anything_else(self):
        self.mock_auth_repo.get_banned_device.return_value = {"id": 1}
        self._set_table_status(TableStatus.OCCUPIED.value)

        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(self._order())

        self.assertEqual(ctx.exception.status_code, 403)
        self.mock_siparis_repo.create_siparis.assert_not_called()

    async def test_missing_table_is_rejected(self):
        self.mock_masa_repo.get_by_id.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            await self.service.create_siparis(self._order(token="123456"))

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_double_submit_inside_the_window_creates_one_order(self):
        self._set_table_status(TableStatus.OCCUPIED.value)
        first = await self.service.create_siparis(self._order())
        second = await self.service.create_siparis(self._order())

        self.assertEqual(first.id, second.id)
        self.mock_siparis_repo.create_siparis.assert_called_once()

    async def test_server_price_overrides_the_client_price_on_create(self):
        self._set_table_status(TableStatus.OCCUPIED.value)
        data = self._order(adet=2)
        data.toplam_tutar = 1.0  # istemci 1 TL iddia ediyor

        order = await self.service.create_siparis(data)

        self.assertEqual(order.toplam_tutar, 200.0)
        written_total = self.mock_siparis_repo.create_siparis.call_args[0][2]
        self.assertEqual(written_total, 200.0)


if __name__ == "__main__":
    unittest.main()
