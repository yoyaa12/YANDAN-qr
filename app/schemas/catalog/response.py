"""Menü uçlarının döndürdüğü yanıt nesneleri.

Entity ile arasındaki fark bilinçlidir: `UrunEntity` `aktif_mi` taşır,
`UrunResponse` taşımaz — hangi ürünün pasifleştirildiği operasyonel bir
detaydır, müşteri menüsünün bilmesi gerekmez. Fiyat da burada `float`'a
çevrilir; `Decimal` JSON'da doğrudan temsil edilemez.
"""

from typing import List, Optional

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


class UrunOpsiyonResponse(BaseModel):
    """Menünün ürün seçeneklerini çizmek için ihtiyaç duyduğu her şey.

    Fiyat farkı istemciye de gönderilir: müşteri sepette tutarı anında görmeli.
    Ancak bu rakam yalnızca GÖSTERİM içindir; sipariş oluşurken sunucu fiyatı
    istemcinin gönderdiği değere bakmadan bu tablodan yeniden okur
    (`SiparisService._calculate_item_authoritative_price`).
    """

    id: int
    grup: str
    kod: str
    ad: str
    aciklama: Optional[str] = None
    fiyat_farki: float
    fiyat_carpani: Optional[float] = None


class KaldirilanMenuResponse(BaseModel):
    """Menüden kaldırılmış (pasifleştirilmiş) ürün ve kategoriler.

    Yalnızca yönetici panelinin "geri getir" ekranı için. Kaldırma yumuşak
    olduğu için bu kayıtlar veritabanında durmaya devam eder; bu uç onları
    görünür kılar, aksi halde geri getirmenin tek yolu veritabanına elle
    müdahale olurdu.
    """

    kategoriler: List[KategoriResponse] = []
    urunler: List[UrunResponse] = []
