"""`Kategoriler` tablosunun veri erişimi.

Satır şeması: `app/schemas/catalog/entity.py` -> `KategoriEntity`.
"""

from typing import List, Optional

from fastapi import Depends

from app.database import DatabaseSession, get_db
from app.schemas.catalog.entity import KategoriEntity


class KategoriRepository:
    def __init__(self, db: DatabaseSession = Depends(get_db)):
        self.db = db

    def get_all_active(self) -> List[KategoriEntity]:
        """Yalnızca `aktif_mi = 1` olan kategoriler; pasifler menüde görünmez."""
        query = "SELECT id, kategori_adi, gorsel_url, aktif_mi FROM Kategoriler WHERE aktif_mi = 1 ORDER BY id ASC"
        return self.db.execute_query(query) or []

    def create(self, kategori_adi: str) -> Optional[int]:
        """Kategoriyi oluşturur ve yeni satırın id'sini döner."""
        query = "INSERT INTO Kategoriler (kategori_adi, aktif_mi) VALUES (?, 1)"
        return self.db.execute_non_query(query, (kategori_adi,))

    def delete(self, kategori_id: int) -> None:
        query = "DELETE FROM Kategoriler WHERE id = ?"
        self.db.execute_non_query(query, (kategori_id,))
