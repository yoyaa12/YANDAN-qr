"""`Urunler` ve `Kategoriler` tablolarının satır şekilleri.

Tip seçimi neden `TypedDict` — ayrıntı için bkz. `app/schemas/auth/entity.py`.

Dikkat edilmesi gereken nokta: `decimal(10,2)` kolonları sürücüden
`decimal.Decimal` olarak gelir, `float` olarak değil. Servis katmanı fiyatla
aritmetik yapmadan önce bunu bilinçli olarak `float()`'a çevirir
(`SiparisService._calculate_item_authoritative_price`). Entity'nin bunu
`Decimal` diye deklare etmesinin amacı da bu: `Decimal * float` çalışmaz ve
dönüşümün nerede yapılması gerektiğini okurken görmek gerekir.

Alanlar 2026-08-20 tarihinde canlı şemadan doğrulanmıştır.
"""

from decimal import Decimal
from typing import Optional, TypedDict


class UrunEntity(TypedDict):
    """`Urunler` tablosunun tam satırı (`SELECT * FROM Urunler`).

    DDL:
        id           int      IDENTITY PRIMARY KEY
        kategori_id  int      NOT NULL  -> Kategoriler.id
        urun_adi     nvarchar(100)  NOT NULL
        aciklama     nvarchar(500)  NULL
        fiyat        decimal(10,2)  NOT NULL
        gorsel_url   nvarchar(255)  NULL
        stok_miktari int            NULL
        aktif_mi     bit            NULL   -- silme yerine pasifleştirme
    """

    id: int
    kategori_id: int
    urun_adi: str
    aciklama: Optional[str]
    fiyat: Decimal
    gorsel_url: Optional[str]
    stok_miktari: Optional[int]
    aktif_mi: Optional[bool]


class UrunWithKategoriEntity(UrunEntity):
    """`Urunler` satırı + `Kategoriler` ile JOIN'den gelen kategori adı.

    Menü listesi ürünü kategori adıyla birlikte gösterir; ayrı bir sorgu
    yerine tek JOIN ile alınır.
    """

    kategori_adi: str


class UrunOpsiyonEntity(TypedDict):
    """`UrunOpsiyonlari` tablosunun tam satırı.

    Bu tablo, opsiyon fiyat farklarının TEK kaynağıdır. Önceden aynı sayılar
    hem `static/js/app.js` içinde hem `SiparisService` içinde elle yazılıydı ve
    aralarındaki tek bağ Türkçe bir metindi; fiyat, müşterinin sipariş notundan
    türetiliyordu.

    Bir opsiyon ya taban fiyata EKLER (`fiyat_farki`) ya da onu ÇARPAR
    (`fiyat_carpani`); ikisi birden dolu olamaz, veritabanındaki
    `CK_UrunOpsiyonlari_tek_mekanizma` kısıtı bunu garanti eder.

    `kod` istemcinin davranış dayanağıdır (örneğin hediye içecek yalnızca
    `medium` ve `jumbo` boylarında sorulur). Ada bakmak yerine koda bakmak,
    ürün adı değiştiğinde arayüzün sessizce bozulmasını engeller.

    DDL:
        id            int            IDENTITY PRIMARY KEY
        grup          nvarchar(20)   NOT NULL  -- 'boy' | 'porsiyon' | 'ekstra'
        kod           nvarchar(30)   NOT NULL  UNIQUE
        ad            nvarchar(100)  NOT NULL
        aciklama      nvarchar(200)  NULL
        fiyat_farki   decimal(10,2)  NOT NULL DEFAULT 0
        fiyat_carpani decimal(6,3)   NULL
        siralama      int            NOT NULL DEFAULT 0
        aktif_mi      bit            NOT NULL DEFAULT 1
    """

    id: int
    grup: str
    kod: str
    ad: str
    aciklama: Optional[str]
    fiyat_farki: Decimal
    fiyat_carpani: Optional[Decimal]
    siralama: int
    aktif_mi: bool


class KategoriEntity(TypedDict):
    """`Kategoriler` tablosunun tam satırı.

    DDL:
        id           int      IDENTITY PRIMARY KEY
        kategori_adi nvarchar(50)   NOT NULL
        aktif_mi     bit            NULL
        gorsel_url   nvarchar(255)  NULL
    """

    id: int
    kategori_adi: str
    aktif_mi: Optional[bool]
    gorsel_url: Optional[str]
