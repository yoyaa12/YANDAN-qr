from fastapi import Depends
from typing import Optional, List
from app.core.events import event_bus
from app.repositories.urun_repo import UrunRepository
from app.schemas.catalog import UrunEkleModel, UrunGuncelleModel, UrunResponse
from app.database import db_transaction

class UrunService:
    def __init__(self, repo: UrunRepository = Depends()):
        self.repo = repo

    def get_urunler(self, kategori_id: Optional[int] = None) -> List[UrunResponse]:
        urunler = self.repo.get_all(kategori_id)
        return [UrunResponse(**u) for u in urunler] if urunler else []

    def add_urun(self, data: UrunEkleModel) -> Optional[int]:
        with db_transaction():
            inserted_id = self.repo.create(data.kategori_id, data.urun_adi, data.aciklama, data.fiyat, data.gorsel_url, data.stok_miktari)
        return inserted_id

    async def update_urun(self, urun_id: int, data: UrunGuncelleModel) -> None:
        updates = {
            "urun_adi": data.urun_adi,
            "fiyat": data.fiyat,
            "aciklama": data.aciklama,
            "stok_miktari": data.stok_miktari
        }
        with db_transaction():
            self.repo.update(urun_id, updates)

        # Admin stoğu elle değiştirdiğinde açık menülerdeki "Son X Adet" rozeti
        # de anında güncellenmelidir; aksi halde müşteri bir sonraki tazelemeye
        # kadar eski adedi görür.
        if data.stok_miktari is not None:
            await event_bus.publish(
                "stok_guncellendi",
                {"stoklar": [{"urun_id": urun_id, "stok_miktari": int(data.stok_miktari)}]},
            )

    def delete_urun(self, urun_id: int) -> None:
        with db_transaction():
            self.repo.delete(urun_id)
