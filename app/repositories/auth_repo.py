from fastapi import Depends
from app.database import DatabaseSession, get_db
from app.enums import UserRole

class AuthRepository:
    def __init__(self, db: DatabaseSession = Depends(get_db)):
        self.db = db

    def get_user_by_username(self, kullanici_adi: str):
        query = """
            SELECT id, kullanici_adi, rol, sifre_hash
            FROM Kullanicilar
            WHERE kullanici_adi = ?
        """
        return self.db.execute_query(query, (kullanici_adi,), fetch_one=True)

    def get_staff_by_id(self, user_id: int):
        query = """
            SELECT id, kullanici_adi, rol
            FROM Kullanicilar
            WHERE id = ?
        """
        return self.db.execute_query(query, (user_id,), fetch_one=True)
    
    def get_garson_credentials(self):
        query = """
            SELECT id, kullanici_adi AS garson_adi, rol, sifre_hash
            FROM Kullanicilar
            WHERE rol = ?
            ORDER BY id ASC
        """
        return self.db.execute_query(query, (UserRole.WAITER.value,)) or []

    def get_all_garsonlar(self):
        query = "SELECT id, kullanici_adi AS garson_adi FROM Kullanicilar WHERE rol = ? ORDER BY kullanici_adi ASC"
        return self.db.execute_query(query, (UserRole.WAITER.value,)) or []

    def get_banned_device(self, device_id: str):
        return self.db.execute_query("SELECT id FROM BannedDevices WHERE device_id = ?", (device_id,), fetch_one=True)

    def ban_device(self, device_id: str):
        self.db.execute_non_query("INSERT INTO BannedDevices (device_id) VALUES (?)", (device_id,))
