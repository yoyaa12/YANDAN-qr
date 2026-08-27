from fastapi import APIRouter, Depends
from typing import Optional, List
from app.schemas.catalog import UrunOpsiyonResponse, UrunResponse
from app.services.urun_service import UrunService

router = APIRouter()

@router.get("/urunler", response_model=List[UrunResponse])
async def get_urunler(
    kategori_id: Optional[int] = None, service: UrunService = Depends()
) -> List[UrunResponse]:
    return service.get_urunler(kategori_id)


@router.get("/urun-opsiyonlari", response_model=List[UrunOpsiyonResponse])
async def get_urun_opsiyonlari(
    service: UrunService = Depends(),
) -> List[UrunOpsiyonResponse]:
    """Urun secenekleri ve fiyat farklari.

    Menu ile ayni erisim seviyesinde (kimliksiz): musteri menuyu acar acmaz
    secenekleri cizebilmeli. Burada donen fiyat farki yalnizca gosterim
    icindir; siparis olusurken sunucu ayni tablodan yeniden okur ve istemcinin
    gonderdigi tutara bakmaz.
    """
    return service.get_opsiyonlar()
