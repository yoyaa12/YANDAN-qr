import socketio
from urllib.parse import parse_qs
from app.core.events import event_bus
from app.auth.tokens import decode_access_token, TokenValidationError, AuthConfigurationError
from app.enums import TokenType, UserRole
from app.database import DatabaseSession
from app.repositories.auth_repo import AuthRepository
from app.services.auth_service import AuthService

# Socket.io Async Sunucusu Oluşturma
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# Masada menüyü inceleyen / sepete ürün ekleyen masaların sunucu tarafında takibi
BROWSING_TABLES = {}
SID_TO_MASA = {}
MASA_SESSIONS = {}


def _extract_token_and_params(auth, environ):
    token = None
    masa_id_param = None

    if auth and isinstance(auth, dict):
        token = auth.get("token") or auth.get("Authorization") or auth.get("token_hash")
        if "masa_id" in auth:
            try:
                masa_id_param = int(auth["masa_id"])
            except (ValueError, TypeError):
                pass

    if "QUERY_STRING" in environ and environ["QUERY_STRING"]:
        qs = parse_qs(environ["QUERY_STRING"])
        if not token and "token" in qs and qs["token"]:
            token = qs["token"][0]
        if not masa_id_param and "masa_id" in qs and qs["masa_id"]:
            try:
                masa_id_param = int(qs["masa_id"][0])
            except (ValueError, TypeError):
                pass

    if token and token.startswith("Bearer "):
        token = token[7:].strip()

    return token, masa_id_param


@sio.event
async def connect(sid, environ, auth=None):
    token, masa_id_param = _extract_token_and_params(auth, environ)
    auth_data = {"user_type": "ANONYMOUS", "masa_id": masa_id_param}

    if token:
        # 1. Staff JWT Token Kontrolü
        if len(token.split(".")) == 3:
            try:
                claims = decode_access_token(token, expected_type=TokenType.STAFF)
                db = DatabaseSession()
                repo = AuthRepository(db)
                user = repo.get_staff_by_id(claims.subject)
                if user and user.get("rol") == claims.role.value:
                    role_val = claims.role.value
                    sio.enter_room(sid, f"role_{role_val}")
                    sio.enter_room(sid, "staff")
                    auth_data = {
                        "user_type": "STAFF",
                        "role": role_val,
                        "user_id": claims.subject,
                        "username": user.get("kullanici_adi")
                    }
                    print(f"[Socket.io] Personel bağlandı: {user.get('kullanici_adi')} (Rol: {role_val}, sid: {sid})")
            except (TokenValidationError, AuthConfigurationError, Exception) as exc:
                print(f"[Socket.io] Personel token doğrulama hatası: {exc}")

        # 2. Müşteri Session Token Kontrolü (Hex Token)
        else:
            try:
                db = DatabaseSession()
                repo = AuthRepository(db)
                service = AuthService(repo)
                customer_session = service.verify_customer_session(token)
                if customer_session:
                    masa_id = int(customer_session["masa_id"])
                    sio.enter_room(sid, f"table_{masa_id}")
                    SID_TO_MASA[sid] = masa_id
                    auth_data = {
                        "user_type": "CUSTOMER",
                        "masa_id": masa_id,
                        "session_token": token
                    }
                    print(f"[Socket.io] Müşteri bağlandı: Masa {masa_id} (sid: {sid})")
            except Exception as exc:
                print(f"[Socket.io] Müşteri session doğrulama hatası: {exc}")

    # 3. Anonim/Public İstemci Masa Odası Katılımı
    if auth_data["user_type"] == "ANONYMOUS" and masa_id_param:
        sio.enter_room(sid, f"table_{masa_id_param}")
        SID_TO_MASA[sid] = masa_id_param
        print(f"[Socket.io] Anonim istemci bağlandı: Masa {masa_id_param} (sid: {sid})")

    await sio.save_session(sid, auth_data)


@sio.event
async def disconnect(sid):
    print(f"[Socket.io] İstemci ayrıldı: {sid}")
    if sid in SID_TO_MASA:
        masa_id = SID_TO_MASA[sid]
        del SID_TO_MASA[sid]
        if masa_id in MASA_SESSIONS and sid in MASA_SESSIONS[masa_id]:
            MASA_SESSIONS[masa_id].remove(sid)
            if not MASA_SESSIONS[masa_id]:
                clear_browsing_table(masa_id)
                await sio.emit("masa_temizlendi", {"masa_id": masa_id}, room="staff")


@sio.event
async def musteri_oturdu(sid, data):
    session = await sio.get_session(sid)
    masa_id = session.get("masa_id")

    if not masa_id and data and isinstance(data, dict) and "masa_id" in data:
        try:
            masa_id = int(data["masa_id"])
        except (ValueError, TypeError):
            pass

    if masa_id:
        SID_TO_MASA[sid] = masa_id
        if masa_id not in MASA_SESSIONS:
            MASA_SESSIONS[masa_id] = set()
        MASA_SESSIONS[masa_id].add(sid)

        if masa_id not in BROWSING_TABLES:
            BROWSING_TABLES[masa_id] = {
                "masa_id": masa_id,
                "masa_no": (data and data.get("masa_no")) or f"Masa {masa_id}",
                "item_count": 0,
                "last_item": ""
            }

    event_payload = {
        "masa_id": masa_id,
        "masa_no": (data and data.get("masa_no")) or f"Masa {masa_id}"
    }
    await sio.emit("garson_musteri_geldi", event_payload, room="role_garson")
    await sio.emit("garson_musteri_geldi", event_payload, room="role_admin")


@sio.event
async def musteri_urun_secti(sid, data):
    session = await sio.get_session(sid)
    masa_id = session.get("masa_id")

    if not masa_id and data and isinstance(data, dict) and "masa_id" in data:
        try:
            masa_id = int(data["masa_id"])
        except (ValueError, TypeError):
            pass

    if masa_id and data and isinstance(data, dict):
        item_count = data.get("item_count", 0)
        BROWSING_TABLES[masa_id] = {
            "masa_id": masa_id,
            "masa_no": data.get("masa_no", f"Masa {masa_id}"),
            "item_count": item_count,
            "last_item": data.get("last_item", "")
        }

    event_payload = {
        "masa_id": masa_id,
        "masa_no": (data and data.get("masa_no")) or f"Masa {masa_id}",
        "item_count": (data and data.get("item_count")) or 0,
        "last_item": (data and data.get("last_item")) or ""
    }
    await sio.emit("garson_musteri_urun_secti", event_payload, room="role_garson")
    await sio.emit("garson_musteri_urun_secti", event_payload, room="role_admin")


def get_browsing_tables():
    return BROWSING_TABLES


def clear_browsing_table(masa_id: int):
    if masa_id in BROWSING_TABLES:
        del BROWSING_TABLES[masa_id]


# --- EventBus Subscriptions (Oda İzoleli Yayınlar) ---

@event_bus.subscribe("yeni_siparis")
async def on_yeni_siparis(payload):
    await sio.emit("yeni_siparis", payload, room="role_mutfak")
    await sio.emit("yeni_siparis", payload, room="role_garson")
    await sio.emit("yeni_siparis", payload, room="role_kasa")
    await sio.emit("yeni_siparis", payload, room="role_admin")


@event_bus.subscribe("masa_durumu_degisti")
async def on_masa_durumu_degisti(payload):
    await sio.emit("masa_durumu_degisti", payload, room="staff")
    if isinstance(payload, dict) and "masa_id" in payload:
        try:
            masa_id = int(payload["masa_id"])
            await sio.emit("masa_durumu_degisti", payload, room=f"table_{masa_id}")
        except (ValueError, TypeError):
            pass


@event_bus.subscribe("garson_onay_talebi")
async def on_garson_onay_talebi(payload):
    await sio.emit("garson_onay_talebi", payload, room="role_garson")
    await sio.emit("garson_onay_talebi", payload, room="role_kasa")
    await sio.emit("garson_onay_talebi", payload, room="role_admin")


@event_bus.subscribe("nakit_odeme_talebi")
async def on_nakit_odeme_talebi(payload):
    await sio.emit("nakit_odeme_talebi", payload, room="role_garson")
    await sio.emit("nakit_odeme_talebi", payload, room="role_kasa")
    await sio.emit("nakit_odeme_talebi", payload, room="role_admin")


@event_bus.subscribe("nakit_odendi")
async def on_nakit_odendi(payload):
    await sio.emit("nakit_odendi", payload, room="role_garson")
    await sio.emit("nakit_odendi", payload, room="role_kasa")
    await sio.emit("nakit_odendi", payload, room="role_admin")


@event_bus.subscribe("durum_guncellendi")
async def on_durum_guncellendi(payload):
    await sio.emit("durum_guncellendi", payload, room="staff")

    masa_id = None
    if isinstance(payload, dict):
        if "masa_id" in payload:
            masa_id = payload["masa_id"]
        elif "siparis" in payload and isinstance(payload["siparis"], dict):
            masa_id = payload["siparis"].get("masa_id")

    if masa_id:
        try:
            m_id = int(masa_id)
            await sio.emit("durum_guncellendi", payload, room=f"table_{m_id}")
        except (ValueError, TypeError):
            pass


@event_bus.subscribe("masa_temizlendi")
async def on_masa_temizlendi(payload):
    await sio.emit("masa_temizlendi", payload, room="staff")
    if isinstance(payload, dict) and "masa_id" in payload:
        try:
            m_id = int(payload["masa_id"])
            await sio.emit("masa_temizlendi", payload, room=f"table_{m_id}")
        except (ValueError, TypeError):
            pass


@event_bus.subscribe("masa_tasindi")
async def on_masa_tasindi(payload):
    if isinstance(payload, dict) and "from_masa_id" in payload and "to_masa_id" in payload:
        try:
            from_id = int(payload["from_masa_id"])
            to_id = int(payload["to_masa_id"])

            if from_id in MASA_SESSIONS:
                sids = MASA_SESSIONS[from_id]
                if to_id not in MASA_SESSIONS:
                    MASA_SESSIONS[to_id] = set()
                MASA_SESSIONS[to_id].update(sids)
                del MASA_SESSIONS[from_id]

                for sid in sids:
                    SID_TO_MASA[sid] = to_id
                    sio.leave_room(sid, f"table_{from_id}")
                    sio.enter_room(sid, f"table_{to_id}")

            if from_id in BROWSING_TABLES:
                data = BROWSING_TABLES.pop(from_id)
                data["masa_id"] = to_id
                data["masa_no"] = payload.get("to_masa_no", f"Masa {to_id}")
                BROWSING_TABLES[to_id] = data
        except Exception as e:
            print(f"[Socket.io] Error updating session on table move: {e}")

    await sio.emit("masa_tasindi", payload, room="staff")
    if isinstance(payload, dict) and "from_masa_id" in payload:
        try:
            f_id = int(payload["from_masa_id"])
            t_id = int(payload.get("to_masa_id", f_id))
            await sio.emit("masa_tasindi", payload, room=f"table_{f_id}")
            await sio.emit("masa_tasindi", payload, room=f"table_{t_id}")
        except (ValueError, TypeError):
            pass
