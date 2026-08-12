import uuid
import datetime
import time
from fastapi import Depends, HTTPException
from typing import Optional, List

from app.core.events import event_bus
from app.core.socket_manager import clear_browsing_table
from app.auth.models import StaffPrincipal
from app.enums import OrderAction, OrderStatus, PaymentMethod, PaymentStatus, TableStatus
from app.repositories.siparis_repo import SiparisRepository
from app.repositories.masa_repo import MasaRepository
from app.repositories.urun_repo import UrunRepository
from app.repositories.auth_repo import AuthRepository
from app.schemas.orders import DurumGuncelleModel, SiparisDuzenleModel, SiparisOlusturModel
from app.schemas.orders import SiparisDurumResponse, SiparisResponse
from app.services.order_authorization import enforce_order_status_role, validate_order_state_transition
from app.database import db_transaction

def sanitize_for_json(data):
    if isinstance(data, dict):
        return {k: sanitize_for_json(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_json(item) for item in data]
    elif isinstance(data, (datetime.datetime, datetime.date, datetime.time)):
        return data.strftime("%H:%M:%S") if isinstance(data, (datetime.datetime, datetime.time)) else str(data)
    import decimal
    if isinstance(data, decimal.Decimal):
        return float(data)
    return data

TABLE_MOVES_MAP = {}
_RECENT_ORDERS_CACHE = {}
_IDEMPOTENCY_WINDOW_SECONDS = 5

class SiparisService:
    def __init__(
        self, 
        siparis_repo: SiparisRepository = Depends(),
        masa_repo: MasaRepository = Depends(),
        urun_repo: UrunRepository = Depends(),
        auth_repo: AuthRepository = Depends()
    ):
        self.siparis_repo = siparis_repo
        self.masa_repo = masa_repo
        self.urun_repo = urun_repo
        self.auth_repo = auth_repo

    def _determine_initial_status(self, odeme_yontemi: PaymentMethod):
        odeme_durumu = (
            PaymentStatus.PAID.value
            if odeme_yontemi == PaymentMethod.POS
            else PaymentStatus.PENDING.value
        )
        
        if odeme_yontemi == PaymentMethod.POS:
            siparis_durumu = OrderStatus.PAID_IN_KITCHEN.value
        elif odeme_yontemi == PaymentMethod.WAITER_AT_CASHIER:
            siparis_durumu = OrderStatus.WAITER_APPROVAL_PENDING.value
        else:
            siparis_durumu = OrderStatus.CASH_PENDING.value
            
        return odeme_durumu, siparis_durumu

    def _calculate_item_authoritative_price(self, u_info: dict, item) -> tuple[float, float]:
        base_price = float(u_info.get("fiyat", 0.0))
        calculated_unit_price = base_price
        note = (item.urun_notu or "").strip()

        if "Orta Boy" in note:
            calculated_unit_price += 40.0
        elif "Büyük Boy" in note:
            calculated_unit_price += 85.0
        elif "En Büyük Boy" in note:
            calculated_unit_price += 140.0

        if "1.5 Porsiyon" in note:
            calculated_unit_price += round(base_price * 0.40, 2)
        elif "2 Porsiyon" in note or "Çift Porsiyon" in note:
            calculated_unit_price += round(base_price * 0.80, 2)

        if "Manda Kaymağı" in note:
            calculated_unit_price += 35.0
        if "Maraş Dondurması" in note:
            calculated_unit_price += 40.0
        if "Çikolata Sosu" in note:
            calculated_unit_price += 25.0
        if "Antep Fıstığı" in note:
            calculated_unit_price += 30.0

        expected_unit_price = round(calculated_unit_price, 2)

        if item.birim_fiyat < base_price:
            raise HTTPException(
                status_code=400,
                detail=f"'{u_info.get('urun_adi')}' için gönderilen birim fiyat ({item.birim_fiyat} TL) veritabanı taban fiyatından ({base_price} TL) düşük olamaz."
            )

        line_total = round(item.adet * expected_unit_price, 2)
        return expected_unit_price, line_total

    def _process_order_items(self, siparis_id: int, urunler: list) -> List[dict]:
        detaylar = []
        for item in urunler:
            u_info = self.urun_repo.get_by_id(item.urun_id)
            if not u_info:
                raise HTTPException(status_code=404, detail=f"Siparişteki Ürün #{item.urun_id} veritabanında bulunamadı!")

            if not u_info.get("aktif_mi", True):
                raise HTTPException(status_code=400, detail=f"'{u_info.get('urun_adi')}' isimli ürün satışa kapalıdır.")

            current_stock = u_info.get("stok_miktari")
            if current_stock is not None and current_stock < item.adet:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{u_info.get('urun_adi')}' için yetersiz stok! (Mevcut stok: {current_stock}, İstenen: {item.adet})"
                )

            authoritative_unit_price, ara_toplam = self._calculate_item_authoritative_price(u_info, item)

            self.siparis_repo.create_siparis_detay(
                siparis_id, item.urun_id, item.adet, authoritative_unit_price, item.urun_notu or "", ara_toplam
            )

            self.urun_repo.update_stock(item.urun_id, item.adet)

            detaylar.append({
                "urun_id": item.urun_id,
                "urun_adi": u_info.get("urun_adi", f"Ürün #{item.urun_id}"),
                "adet": item.adet,
                "birim_fiyat": authoritative_unit_price,
                "urun_notu": item.urun_notu or "",
                "ara_toplam": ara_toplam
            })
        return detaylar

    async def _publish_order_events(self, data: SiparisOlusturModel, siparis_id: int, masa_no: str, order_dict: dict):
        if data.odeme_yontemi == PaymentMethod.POS:
            await event_bus.publish("yeni_siparis", order_dict)

        await event_bus.publish(
            "masa_durumu_degisti",
            {"masa_id": data.masa_id, "durum": TableStatus.OCCUPIED.value},
        )
        await event_bus.publish("durum_guncellendi", order_dict)
        
        if data.odeme_yontemi == PaymentMethod.WAITER_AT_CASHIER:
            await event_bus.publish("garson_onay_talebi", {
                "siparis_id": siparis_id,
                "masa_id": data.masa_id,
                "masa_no": masa_no,
                "toplam_tutar": data.toplam_tutar,
                "siparis": order_dict
            })
        elif data.odeme_yontemi == PaymentMethod.CASH:
            await event_bus.publish("nakit_odeme_talebi", {
                "siparis_id": siparis_id,
                "masa_id": data.masa_id,
                "masa_no": masa_no,
                "toplam_tutar": data.toplam_tutar,
                "siparis": order_dict
            })

    async def create_siparis(self, data: SiparisOlusturModel) -> SiparisResponse:
        if data.device_id:
            banned = self.auth_repo.get_banned_device(data.device_id)
            if banned:
                raise HTTPException(status_code=403, detail="Erişiminiz engellendi. Cihazınız yasaklı.")

        items_key = tuple(sorted((item.urun_id, item.adet, (item.urun_notu or "").strip()) for item in data.urunler))
        cache_key = (data.masa_id, data.device_id or "no-device", items_key)
        now = time.time()
        if cache_key in _RECENT_ORDERS_CACHE:
            ts, prev_resp = _RECENT_ORDERS_CACHE[cache_key]
            if now - ts < _IDEMPOTENCY_WINDOW_SECONDS:
                return prev_resp

        with db_transaction():
            if data.masa_id in TABLE_MOVES_MAP:
                data.masa_id = TABLE_MOVES_MAP[data.masa_id]

            masa = self.masa_repo.get_by_id(data.masa_id)
            if not masa:
                raise HTTPException(status_code=404, detail="Geçersiz masa ID!")

            if masa.get('durum') == TableStatus.EMPTY.value:
                if not data.current_totp_token:
                    raise HTTPException(status_code=403, detail="Masa şu an BOŞ. İlk siparişi vermek için lütfen masadaki ekranın altında yazan 6 haneli güvenlik kodunu okutun.")
                
                from app.core.totp_service import verify_dynamic_token
                totp_secret = masa.get("totp_secret")
                if not totp_secret or not verify_dynamic_token(data.masa_id, totp_secret, data.current_totp_token, mark_as_used=True):
                    raise HTTPException(status_code=403, detail="Geçersiz veya süresi dolmuş kod! Lütfen masadaki ekranda yazan güncel 6 haneli güvenlik kodunu girin.")

            calculated_order_total = 0.0
            for item in data.urunler:
                u_info = self.urun_repo.get_by_id(item.urun_id)
                if not u_info:
                    raise HTTPException(status_code=404, detail=f"Siparişteki Ürün #{item.urun_id} veritabanında bulunamadı!")
                if not u_info.get("aktif_mi", True):
                    raise HTTPException(status_code=400, detail=f"'{u_info.get('urun_adi')}' isimli ürün satışa kapalıdır.")
                current_stock = u_info.get("stok_miktari")
                if current_stock is not None and current_stock < item.adet:
                    raise HTTPException(
                        status_code=400,
                        detail=f"'{u_info.get('urun_adi')}' için yetersiz stok! (Mevcut stok: {current_stock}, İstenen: {item.adet})"
                    )
                _, line_total = self._calculate_item_authoritative_price(u_info, item)
                calculated_order_total += line_total

            calculated_order_total = round(calculated_order_total, 2)
            data.toplam_tutar = calculated_order_total

            siparis_kodu = f"SIP-{uuid.uuid4().hex[:6].upper()}"
            odeme_durumu, siparis_durumu = self._determine_initial_status(data.odeme_yontemi)

            siparis_id = self.siparis_repo.create_siparis(
                data.masa_id,
                siparis_kodu,
                data.toplam_tutar,
                odeme_durumu,
                siparis_durumu,
                data.device_id,
            )

            if not siparis_id:
                raise HTTPException(status_code=500, detail="Sipariş veritabanına eklenirken hata oluştu.")

            self.masa_repo.update_durum(data.masa_id, TableStatus.OCCUPIED.value)
            detaylar = self._process_order_items(siparis_id, data.urunler)
            clear_browsing_table(data.masa_id)

            full_order_dict = {
                "id": siparis_id,
                "masa_id": data.masa_id,
                "masa_no": masa['masa_no'],
                "siparis_kodu": siparis_kodu,
                "toplam_tutar": data.toplam_tutar,
                "odeme_yontemi": data.odeme_yontemi.value,
                "odeme_durumu": odeme_durumu,
                "siparis_durumu": siparis_durumu,
                "olusturma_tarihi": datetime.datetime.now().strftime("%H:%M:%S"),
                "garson_adi": None,
                "device_id": data.device_id,
                "detaylar": detaylar
            }
            full_order = SiparisResponse.model_validate(full_order_dict)

        _RECENT_ORDERS_CACHE[cache_key] = (now, full_order)

        await self._publish_order_events(
            data,
            siparis_id,
            masa['masa_no'],
            full_order.model_dump(mode="json"),
        )
        return full_order

    def _map_to_siparis_response(self, order_dict: dict) -> SiparisResponse:
        order_dict['detaylar'] = self.siparis_repo.get_siparis_detaylari(order_dict['id'])
        for d in order_dict['detaylar']:
            d['urun_notu'] = d.get('urun_notu') or ""
        
        if isinstance(order_dict.get('olusturma_tarihi'), datetime.datetime):
            order_dict['olusturma_tarihi'] = order_dict['olusturma_tarihi'].strftime("%H:%M:%S")
            
        return SiparisResponse.model_validate(order_dict)

    def get_siparisler(self, durum: Optional[str] = None, masa_id: Optional[int] = None) -> List[SiparisResponse]:
        siparisler = self.siparis_repo.get_all(durum, masa_id)
        return [self._map_to_siparis_response(s) for s in siparisler]

    def get_masa_aktif_siparis(self, masa_id: int):
        target_masa_id = masa_id
        is_redirected = False

        if masa_id in TABLE_MOVES_MAP:
            target_masa_id = TABLE_MOVES_MAP[masa_id]
            is_redirected = True

        siparisler = self.siparis_repo.get_all_active_by_masa_id(target_masa_id)
        if siparisler:
            s_dtos = [self._map_to_siparis_response(s) for s in siparisler]
            genel_toplam = sum(s.toplam_tutar for s in s_dtos if s.toplam_tutar)
            res = {
                "has_active": True,
                "siparisler": [s.model_dump(mode="json") for s in s_dtos],
                "siparis": s_dtos[-1].model_dump(mode="json"),
                "genel_toplam": genel_toplam
            }
        else:
            res = {"has_active": False, "siparisler": [], "siparis": None, "genel_toplam": 0.0}

        if is_redirected:
            t_table = self.masa_repo.get_by_id(target_masa_id)
            res["redirect_masa_id"] = target_masa_id
            res["redirect_masa_no"] = t_table.get("masa_no", f"Masa {target_masa_id}") if t_table else f"Masa {target_masa_id}"
        return res

    async def update_siparis_durumu(
        self,
        siparis_id: int,
        data: DurumGuncelleModel,
        principal: StaffPrincipal,
    ) -> SiparisDurumResponse:
        enforce_order_status_role(principal.role, data.yeni_durum)
        yeni_durum = data.yeni_durum.value
        garson_adi = data.garson_adi or "Garson Berat"
        masa_bosaldi = False

        with db_transaction():
            s_info = self.siparis_repo.get_by_id(siparis_id)
            if not s_info:
                raise HTTPException(status_code=404, detail="Sipariş bulunamadı!")

            validate_order_state_transition(s_info.get("siparis_durumu", ""), data.yeni_durum)

            if yeni_durum in [OrderAction.CASH_COLLECTED.value, OrderStatus.PAID_CLOSED.value]:
                self.siparis_repo.update_odeme_and_durum(
                    siparis_id,
                    PaymentStatus.PAID.value,
                    OrderStatus.DELIVERED.value,
                    garson_adi,
                )
                yeni_durum = OrderStatus.DELIVERED.value
            else:
                staff_name_statuses = {
                    OrderStatus.WAITER_APPROVED_IN_KITCHEN.value,
                    OrderStatus.DELIVERED.value,
                }
                self.siparis_repo.update_durum(
                    siparis_id,
                    yeni_durum,
                    garson_adi if yeni_durum in staff_name_statuses else None,
                )

            if yeni_durum in [OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value]:
                aktif_sayi = self.siparis_repo.get_active_count_for_masa(s_info['masa_id'])
                unpaid_sayi = self.siparis_repo.get_unpaid_count_for_masa(s_info['masa_id'])
                if aktif_sayi == 0 and unpaid_sayi == 0:
                    self.masa_repo.update_durum(s_info['masa_id'], TableStatus.EMPTY.value)
                    clear_browsing_table(s_info['masa_id'])
                    masa_bosaldi = True

            updated_order = self.siparis_repo.get_by_id(siparis_id)
            if not updated_order:
                updated_order = dict(s_info)
                updated_order['siparis_durumu'] = yeni_durum
            
            s_dto = self._map_to_siparis_response(updated_order)

            event_payload = SiparisDurumResponse(
                siparis_id=siparis_id,
                masa_id=s_info['masa_id'],
                masa_no=s_info['masa_no'],
                yeni_durum=yeni_durum,
                odeme_durumu=updated_order.get("odeme_durumu", PaymentStatus.PAID.value),
                garson_adi=garson_adi,
                guncelleme_tarihi=datetime.datetime.now().strftime("%H:%M:%S"),
                siparis=s_dto
            )

        payload_dict = event_payload.model_dump(mode="json")
        s_dto_dict = s_dto.model_dump(mode="json")

        if masa_bosaldi:
            await event_bus.publish(
                "masa_durumu_degisti",
                {"masa_id": s_info['masa_id'], "durum": TableStatus.EMPTY.value},
            )

        if data.yeni_durum.value in [
            OrderAction.CASH_COLLECTED.value,
            OrderStatus.WAITER_APPROVED_IN_KITCHEN.value,
        ]:
            await event_bus.publish("yeni_siparis", s_dto_dict)
            await event_bus.publish("nakit_odendi", payload_dict)

        await event_bus.publish("durum_guncellendi", payload_dict)
        return event_payload

    async def clear_masa(self, masa_id: int):
        with db_transaction():
            self.masa_repo.update_durum(masa_id, TableStatus.EMPTY.value)
            self.siparis_repo.clear_active_orders_for_masa(masa_id)
            clear_browsing_table(masa_id)
            TABLE_MOVES_MAP.pop(masa_id, None)
            for k, v in list(TABLE_MOVES_MAP.items()):
                if v == masa_id:
                    TABLE_MOVES_MAP.pop(k, None)
        
        event_payload = {"masa_id": masa_id, "durum": TableStatus.EMPTY.value}
        await event_bus.publish("masa_durumu_degisti", event_payload)
        await event_bus.publish("masa_temizlendi", {"masa_id": masa_id})
        await event_bus.publish(
            "durum_guncellendi",
            {"masa_id": masa_id, "yeni_durum": TableStatus.EMPTY.value},
        )

    async def update_siparis_items(self, siparis_id: int, data: SiparisDuzenleModel) -> SiparisResponse:
        garson_adi = data.garson_adi or "Garson Berat"
        
        with db_transaction():
            s_info = self.siparis_repo.get_by_id(siparis_id)
            if not s_info:
                raise HTTPException(status_code=404, detail="Sipariş bulunamadı!")
            
            self.siparis_repo.update_siparis_items(siparis_id, data.toplam_tutar, data.urunler, garson_adi)
            
            updated_order = self.siparis_repo.get_by_id(siparis_id)
            s_dto = self._map_to_siparis_response(updated_order)

            event_payload = {
                "siparis_id": siparis_id,
                "masa_id": s_info['masa_id'],
                "masa_no": s_info['masa_no'],
                "yeni_durum": s_info.get(
                    "siparis_durumu", OrderStatus.WAITER_APPROVAL_PENDING.value
                ),
                "odeme_durumu": s_info.get("odeme_durumu", PaymentStatus.PENDING.value),
                "garson_adi": garson_adi,
                "guncelleme_tarihi": datetime.datetime.now().strftime("%H:%M:%S"),
                "siparis": s_dto.model_dump(mode="json")
            }

        await event_bus.publish("durum_guncellendi", event_payload)
        return s_dto

    async def move_masa(self, from_masa_id: int, to_masa_id: int):
        with db_transaction():
            self.siparis_repo.move_orders_between_masalar(from_masa_id, to_masa_id)
            from_masa = self.masa_repo.get_by_id(from_masa_id)
            from_masa_no = from_masa.get("masa_no", f"Masa {from_masa_id}") if from_masa else f"Masa {from_masa_id}"
            to_masa = self.masa_repo.get_by_id(to_masa_id)
            to_masa_no = to_masa.get("masa_no", f"Masa {to_masa_id}") if to_masa else f"Masa {to_masa_id}"
            
            self.masa_repo.update_durum(to_masa_id, TableStatus.OCCUPIED.value)
            self.masa_repo.update_durum(from_masa_id, TableStatus.EMPTY.value)
            clear_browsing_table(from_masa_id)
            TABLE_MOVES_MAP[from_masa_id] = to_masa_id
        
        event_payload = {
            "from_masa_id": from_masa_id,
            "from_masa_no": from_masa_no,
            "to_masa_id": to_masa_id,
            "to_masa_no": to_masa_no,
            "is_move": True
        }
        await event_bus.publish("masa_tasindi", event_payload)
        await event_bus.publish(
            "masa_durumu_degisti",
            {"masa_id": to_masa_id, "durum": TableStatus.OCCUPIED.value, "is_move": True},
        )
        await event_bus.publish("durum_guncellendi", event_payload)
