import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from app.enums import UserRole
from app.core.socket_manager import (
    _extract_token_and_params,
    connect,
    on_yeni_siparis,
    on_garson_onay_talebi,
    on_durum_guncellendi,
)


class TestSocketAuthAndIsolation(unittest.TestCase):

    def test_extract_token_from_auth_dict(self):
        auth = {"token": "Bearer test-jwt-token", "masa_id": 5}
        environ = {}
        token, masa_id = _extract_token_and_params(auth, environ)
        self.assertEqual(token, "test-jwt-token")
        self.assertEqual(masa_id, 5)

    def test_extract_token_from_query_string(self):
        auth = None
        environ = {"QUERY_STRING": "token=hex-session-token&masa_id=3"}
        token, masa_id = _extract_token_and_params(auth, environ)
        self.assertEqual(token, "hex-session-token")
        self.assertEqual(masa_id, 3)

    def test_extract_token_from_query_string_variants(self):
        auth = None
        environ = {"QUERY_STRING": "access_token=jwt-token-123&masa_id=7"}
        token, masa_id = _extract_token_and_params(auth, environ)
        self.assertEqual(token, "jwt-token-123")
        self.assertEqual(masa_id, 7)

    def test_room_helpers_are_coroutines(self):
        """sio.enter_room/leave_room python-socketio 5.11+ ile coroutine oldu.

        Bu testler odaya katılımı mock'ladığı için, gerçek koddaki `await`
        eksikliğini ancak `assert_any_await` yakalayabilir. Sözleşme değişirse
        (senkron hale dönerse) burası önce kırılsın.
        """
        import inspect
        from app.core.socket_manager import sio
        self.assertTrue(inspect.iscoroutinefunction(sio.enter_room))
        self.assertTrue(inspect.iscoroutinefunction(sio.leave_room))

    @patch("app.core.socket_manager.sio.enter_room", new_callable=AsyncMock)
    @patch("app.core.socket_manager.sio.get_session", new_callable=AsyncMock)
    def test_transport_upgrade_retains_staff_rooms(self, mock_get_session, mock_enter_room):
        mock_get_session.return_value = {
            "user_type": "STAFF",
            "role": "mutfak",
            "user_id": 2,
            "username": "User_2"
        }
        import asyncio
        asyncio.run(connect("sid-upgrade", {}, None))

        # assert_any_await: çağrının yapılmış olması yetmez, await de edilmiş olmalı.
        mock_enter_room.assert_any_await("sid-upgrade", "role_mutfak")
        mock_enter_room.assert_any_await("sid-upgrade", "staff")

    @patch("app.core.socket_manager.sio.enter_room", new_callable=AsyncMock)
    @patch("app.core.socket_manager.sio.save_session", new_callable=AsyncMock)
    @patch("app.core.socket_manager.AuthRepository")
    @patch("app.core.socket_manager.DatabaseSession")
    @patch("app.core.socket_manager.decode_access_token")
    def test_staff_handshake_joins_staff_rooms(
        self, mock_decode, mock_db, mock_repo_cls, mock_save_session, mock_enter_room
    ):
        mock_claims = MagicMock()
        mock_claims.subject = 1
        mock_claims.role = UserRole.WAITER
        mock_decode.return_value = mock_claims

        mock_repo = MagicMock()
        mock_repo.get_staff_by_id.return_value = {"id": 1, "kullanici_adi": "garson1", "rol": "garson"}
        mock_repo_cls.return_value = mock_repo

        auth = {"token": "header.payload.signature"}
        environ = {}

        import asyncio
        asyncio.run(connect("sid-123", environ, auth))

        mock_enter_room.assert_any_await("sid-123", "role_garson")
        mock_enter_room.assert_any_await("sid-123", "staff")
        mock_save_session.assert_called_once()
        saved = mock_save_session.call_args[0][1]
        self.assertEqual(saved["user_type"], "STAFF")
        self.assertEqual(saved["role"], "garson")

    @patch("app.core.socket_manager.sio.enter_room", new_callable=AsyncMock)
    @patch("app.core.socket_manager.sio.save_session", new_callable=AsyncMock)
    @patch("app.core.socket_manager.AuthService")
    @patch("app.core.socket_manager.AuthRepository")
    @patch("app.core.socket_manager.DatabaseSession")
    def test_customer_handshake_joins_table_room(
        self, mock_db, mock_repo_cls, mock_service_cls, mock_save_session, mock_enter_room
    ):
        mock_service = MagicMock()
        mock_service.verify_customer_session.return_value = {"id": 10, "masa_id": 2, "session_token": "hex123"}
        mock_service_cls.return_value = mock_service

        auth = {"token": "hex1234567890abcdef"}
        environ = {}

        import asyncio
        asyncio.run(connect("sid-cust", environ, auth))

        mock_enter_room.assert_awaited_once_with("sid-cust", "table_2")
        saved = mock_save_session.call_args[0][1]
        self.assertEqual(saved["user_type"], "CUSTOMER")
        self.assertEqual(saved["masa_id"], 2)

    @patch("app.core.socket_manager.sio.emit", new_callable=AsyncMock)
    def test_yeni_siparis_reaches_all_staff_exactly_once(self, mock_emit):
        payload = {"siparis_id": 99, "masa_id": 1, "toplam_tutar": 120.0}

        import asyncio
        asyncio.run(on_yeni_siparis(payload))

        # Her personel soketi hem "staff" hem "role_*" odasındadır. Aynı olayı
        # birden fazla odaya ayrı emit ile göndermek mutfakta çift zil/çift
        # veri çekmeye yol açar; tek çağrı olmalı.
        self.assertEqual(mock_emit.await_count, 1)
        self.assertEqual(mock_emit.await_args.kwargs.get("room"), "staff")

    @patch("app.core.socket_manager.sio.emit", new_callable=AsyncMock)
    def test_garson_onay_talebi_targets_service_roles_without_kitchen(self, mock_emit):
        payload = {"siparis_id": 7, "masa_id": 2, "toplam_tutar": 90.0}

        import asyncio
        asyncio.run(on_garson_onay_talebi(payload))

        self.assertEqual(mock_emit.await_count, 1)
        rooms = mock_emit.await_args.kwargs.get("room")
        self.assertCountEqual(rooms, ["role_garson", "role_kasa", "role_admin"])
        self.assertNotIn("role_mutfak", rooms)

    @patch("app.core.socket_manager.sio.emit", new_callable=AsyncMock)
    def test_events_are_never_broadcast_globally(self, mock_emit):
        """Oda filtresi olmayan emit tüm istemcilere (müşteriler dahil) gider."""
        import asyncio
        for handler in (on_yeni_siparis, on_garson_onay_talebi, on_durum_guncellendi):
            asyncio.run(handler({"siparis_id": 1, "masa_id": 3}))

        self.assertTrue(mock_emit.await_args_list)
        for call in mock_emit.await_args_list:
            self.assertIsNotNone(call.kwargs.get("room"))

    @patch("app.core.socket_manager.sio.emit", new_callable=AsyncMock)
    def test_durum_guncellendi_emits_to_table_and_staff_rooms(self, mock_emit):
        payload = {"siparis_id": 5, "masa_id": 4, "yeni_durum": "hazirlaniyor"}

        import asyncio
        asyncio.run(on_durum_guncellendi(payload))

        emitted_rooms = [call.kwargs.get("room") for call in mock_emit.call_args_list]
        self.assertIn("staff", emitted_rooms)
        self.assertIn("table_4", emitted_rooms)


if __name__ == "__main__":
    unittest.main()
