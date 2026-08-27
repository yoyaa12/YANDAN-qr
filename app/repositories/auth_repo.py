"""`Kullanicilar`, `CustomerSessions` ve `BannedDevices` tablolarının veri erişimi.

Her metodun dönüş tipi `app/schemas/auth/entity.py` içindeki satır şemasına
işaret eder: sorgunun hangi kolonları getirdiğini görmek için SQL metnini
okumak gerekmez, entity'ye bakmak yeterlidir.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import Depends

from app.database import DatabaseSession, get_db
from app.enums import UserRole
from app.schemas.auth.entity import (
    BannedDeviceEntity,
    CustomerSessionEntity,
    GarsonCredentialsEntity,
    GarsonListEntity,
    KullaniciEntity,
    StaffIdentityEntity,
)


class AuthRepository:
    def __init__(self, db: DatabaseSession = Depends(get_db)):
        self.db = db

    def get_user_by_username(self, kullanici_adi: str) -> Optional[KullaniciEntity]:
        """Personel girişi için kullanıcıyı parola hash'iyle birlikte okur."""
        query = """
            SELECT id, kullanici_adi, rol, sifre_hash
            FROM Kullanicilar
            WHERE kullanici_adi = ?
        """
        return self.db.execute_query(query, (kullanici_adi,), fetch_one=True)

    def get_staff_by_id(self, user_id: int) -> Optional[StaffIdentityEntity]:
        """Token doğrulandıktan sonra kullanıcının hâlâ var olduğunu teyit eder.

        Parola hash'i bilinçli olarak seçilmez: bu yol yalnızca kimliği ve rolü
        tazelemek içindir.
        """
        query = """
            SELECT id, kullanici_adi, rol
            FROM Kullanicilar
            WHERE id = ?
        """
        return self.db.execute_query(query, (user_id,), fetch_one=True)

    def get_garson_credentials(self) -> List[GarsonCredentialsEntity]:
        """PIN doğrulaması için tüm garsonları hash'leriyle birlikte okur.

        Garson PIN'i kullanıcı adı olmadan girildiği için hangi kayda ait
        olduğu önceden bilinemez; servis katmanı adayları sırayla dener
        (`AuthService.verify_garson_pin`). Bu yüzden `sifre_hash` burada
        taşınmak zorundadır ve bu nesne asla HTTP yanıtına ulaşmaz.
        """
        query = """
            SELECT id, kullanici_adi AS garson_adi, rol, sifre_hash
            FROM Kullanicilar
            WHERE rol = ?
            ORDER BY id ASC
        """
        return self.db.execute_query(query, (UserRole.WAITER.value,)) or []

    def get_all_garsonlar(self) -> List[GarsonListEntity]:
        """Garson seçim listesi; parola hash'i taşımaz."""
        query = "SELECT id, kullanici_adi AS garson_adi FROM Kullanicilar WHERE rol = ? ORDER BY kullanici_adi ASC"
        return self.db.execute_query(query, (UserRole.WAITER.value,)) or []

    def get_banned_device(self, device_id: str) -> Optional[BannedDeviceEntity]:
        return self.db.execute_query("SELECT id FROM BannedDevices WHERE device_id = ?", (device_id,), fetch_one=True)

    def ban_device(self, device_id: str) -> None:
        self.db.execute_non_query("INSERT INTO BannedDevices (device_id) VALUES (?)", (device_id,))

    def create_customer_session(
        self,
        session_token_hash: str,
        masa_id: int,
        expires_at: datetime,
        device_id: Optional[str] = None,
    ) -> None:
        """Yalnızca token'ın SHA-256 hash'i saklanır, ham token değil."""
        query = """
            INSERT INTO CustomerSessions (session_token_hash, masa_id, device_id, expires_at, is_active)
            VALUES (?, ?, ?, ?, 1)
        """
        self.db.execute_non_query(query, (session_token_hash, masa_id, device_id, expires_at))

    def get_active_customer_session(self, session_token_hash: str) -> Optional[CustomerSessionEntity]:
        query = """
            SELECT id, masa_id, device_id, expires_at
            FROM CustomerSessions
            WHERE session_token_hash = ? AND is_active = 1 AND expires_at > GETDATE()
        """
        return self.db.execute_query(query, (session_token_hash,), fetch_one=True)

    def touch_customer_session(self, session_token_hash: str, expires_at: datetime) -> None:
        """Kayan oturum ömrü: kullanılan oturumun bitiş zamanını ileri atar."""
        query = """
            UPDATE CustomerSessions
            SET expires_at = ?
            WHERE session_token_hash = ? AND is_active = 1
        """
        self.db.execute_non_query(query, (expires_at, session_token_hash))

    def get_active_session_for_device(
        self, masa_id: int, device_id: str
    ) -> Optional[CustomerSessionEntity]:
        """Bir cihazın o masadaki canlı oturumu (varsa).

        Aynı cihaz QR'ı ikinci kez okuttuğunda yeni satır açmak yerine bu satır
        tazelenir; bkz. `AuthService.create_customer_session`.
        """
        query = """
            SELECT TOP 1 id, masa_id, device_id, expires_at
            FROM CustomerSessions
            WHERE masa_id = ?
              AND device_id = ?
              AND is_active = 1
              AND expires_at > GETDATE()
            ORDER BY id DESC
        """
        return self.db.execute_query(query, (masa_id, device_id), fetch_one=True)

    def rotate_customer_session(
        self, session_id: int, session_token_hash: str, expires_at: datetime
    ) -> None:
        """Var olan oturumun token'ını ve bitiş zamanını yerinde yeniler.

        Satır kimliği korunur. `Siparisler.customer_session_id` bu kimliğe
        bakarak "bu sipariş bana ait mi" sorusunu yanıtladığı için, yeni satır
        açmak müşterinin kendi siparişlerini kaybetmesine yol açıyordu.
        """
        query = """
            UPDATE CustomerSessions
            SET session_token_hash = ?, expires_at = ?, is_active = 1
            WHERE id = ?
        """
        self.db.execute_non_query(query, (session_token_hash, expires_at, session_id))

    def revoke_other_sessions_for_device(
        self, masa_id: int, device_id: str, keep_session_id: int
    ) -> int:
        """Aynı cihaz + masa için fazladan kalmış canlı oturumları kapatır.

        Düzeltme öncesi her QR okutması yeni satır açtığı için tek cihaz aynı
        masada birden çok canlı oturum taşıyabiliyordu. Bu yol o birikimi
        temizler; bundan sonrası için zaten tek satır üretilir.
        """
        query = """
            UPDATE CustomerSessions
            SET is_active = 0
            WHERE masa_id = ? AND device_id = ? AND is_active = 1 AND id <> ?
        """
        return self.db.execute_update(query, (masa_id, device_id, keep_session_id))

    def deactivate_expired_customer_sessions(self) -> int:
        """Süresi dolmuş oturumların `is_active` bayrağını düşürür.

        Güvenlik için gerekli değildir: `get_active_customer_session` zaten hem
        `is_active = 1` hem `expires_at > GETDATE()` arar, yani süresi geçmiş
        satır bayrağı 1 olsa da kabul edilmez. Gerekli olan şey okunabilirlik:
        aksi halde tabloya bakan kişi çoktan ölmüş oturumları canlı sanıyor.

        Etkilenen satır sayısını döner (sürücü bildiremezse -1).
        """
        query = """
            UPDATE CustomerSessions
            SET is_active = 0
            WHERE is_active = 1 AND expires_at <= GETDATE()
        """
        return self.db.execute_update(query)

    def revoke_customer_session(self, session_token_hash: str) -> None:
        query = "UPDATE CustomerSessions SET is_active = 0 WHERE session_token_hash = ?"
        self.db.execute_non_query(query, (session_token_hash,))

    def revoke_all_sessions_for_masa(self, masa_id: int) -> None:
        """Adisyon kapanınca masadaki tüm müşteri oturumlarını geçersiz kılar."""
        query = "UPDATE CustomerSessions SET is_active = 0 WHERE masa_id = ?"
        self.db.execute_non_query(query, (masa_id,))

    def move_sessions_to_masa(
        self, session_ids: List[int], from_masa_id: int, to_masa_id: int
    ) -> int:
        """Belirtilen oturumlari kaynak masadan hedef masaya tasir.

        `move_active_sessions_to_masa` masanin TAMAMI tasindiginda kullanilir.
        Bu yol ise kalem tasimasi icindir: adisyonun yalnizca bir kismi
        tasindiginda kaynak masada oturmaya devam eden musteriler vardir ve
        onlarin oturumu yerinde kalmalidir.

        `masa_id = from_masa_id` kosulu bilincli: cagiran yanlis bir kimlik
        gonderse bile baska bir masanin oturumu buradan tasinamaz.

        Etkilenen satir sayisini doner (surucu bildiremezse -1).
        """
        if not session_ids:
            return 0
        placeholders = ", ".join("?" for _ in session_ids)
        query = f"""
            UPDATE CustomerSessions
            SET masa_id = ?
            WHERE masa_id = ? AND is_active = 1 AND id IN ({placeholders})
        """
        return self.db.execute_update(
            query, (to_masa_id, from_masa_id, *session_ids)
        )

    def move_active_sessions_to_masa(self, from_masa_id: int, to_masa_id: int) -> int:
        """Canlı müşteri oturumlarını hedef masaya taşır.

        Adisyon taşınırken yalnızca `Siparisler.masa_id` güncelleniyordu;
        oturumlar kaynak masada kalıyordu. Bunun iki görünür sonucu vardı:

        1. Müşteri kendi siparişlerini kaybediyordu. `is_mine` hesabı
           `Siparisler.customer_session_id` eşitliğine dayanır; istemci hedef
           masaya geçtiğinde eski oturumu o masada geçersiz olduğu için (bkz.
           `masalar.get_masa_aktif_siparis` içindeki masa eşleşme kontrolü)
           QR'ı yeniden okutup YENİ bir oturum satırı açmak zorunda kalıyor,
           taşınan siparişler ise hâlâ eski satırın kimliğini taşıyordu.
        2. Adisyon kapanırken oturumlar geride kalıyordu.
           `revoke_all_sessions_for_masa` hedef masayı süpürür; kaynak masada
           unutulan satırlar TTL dolana kadar `is_active = 1` kalıyordu.

        Etkilenen satır sayısını döner (sürücü bildiremezse -1).
        """
        query = """
            UPDATE CustomerSessions
            SET masa_id = ?
            WHERE masa_id = ? AND is_active = 1
        """
        return self.db.execute_update(query, (to_masa_id, from_masa_id))
