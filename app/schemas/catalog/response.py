"""Menü uçlarının döndürdüğü yanıt nesneleri.

Entity ile arasındaki fark bilinçlidir: `UrunEntity` `aktif_mi` taşır,
`UrunResponse` taşımaz — hangi ürünün pasifleştirildiği operasyonel bir
detaydır, müşteri menüsünün bilmesi gerekmez. Fiyat da burada `float`'a
çevrilir; `Decimal` JSON'da doğrudan temsil edilemez.
"""

from typing import Optional

from pydantic import BaseModel


class UrunResponse(BaseModel):
    id: int
    kategori_id: int
    kategori_adi: Optional[str] = None
    urun_adi: str
    aciklama: Optional[str] = None
    fiyat: float
    gorsel_url: Optional[str] = None
    stok_miktari: int


class KategoriResponse(BaseModel):
    id: int
    kategori_adi: str
    gorsel_url: Optional[str] = None
