"""Sipariş uçlarının kabul ettiği istek gövdeleri.

Bu dosyadaki modellerin ortak ilkesi: istemci **ne istediğini** söyleyebilir,
**ne ödeyeceğini** veya **kim olduğunu** söyleyemez. Fiyat servis katmanında
katalogdan yeniden hesaplanır, kimlik ise doğrulanmış token'dan okunur.
"""

from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.enums import OrderAction, OrderStatus, PaymentMethod


# Tek sipariş kaleminde izin verilen en yüksek adet. Gerçek bir masanın tek
# kalemde isteyebileceğinin çok üzerinde, ama "her üründen maksimum adet
# gönderip tüm stoğu kilitleme" denemesini ve `decimal(10,2)` satır toplamının
# taşmasını engelleyecek kadar düşük. Müşteri arayüzü zaten tek seferde en fazla
# 20 adet eklettiriyor (templates/menu.html `max="20"`).
MAX_LINE_QUANTITY = 50


# Tek kalemde seçilebilecek en fazla opsiyon sayısı. Grup başına yalnızca bir
# opsiyona izin verildiği için (bkz. `SiparisService._resolve_line_options`)
# gerçek tavan zaten grup sayısıdır; bu sınır, uzun bir id listesiyle sorgu
# şişirmeyi engelleyen kaba bir korumadır.
MAX_LINE_OPTIONS = 12


class SiparisItemModel(BaseModel):
    """Sepetteki tek kalem.

    `birim_fiyat` yalnızca istemcinin iddiasıdır: sunucu bunu
    `SiparisService._price_items_authoritatively` içinde `Urunler` tablosundan
    gelen fiyatla değiştirir. Alanın tutulmasının tek nedeni, katalog fiyatının
    altında bir iddianın kurcalama sinyali olarak reddedilebilmesidir.

    `opsiyon_ids` müşterinin TIKLADIĞI seçeneklerin `UrunOpsiyonlari.id`
    değerleridir. Fiyat farkı bu kimlikler üzerinden veritabanından okunur.
    Önceden böyle bir alan yoktu ve sunucu farkı `urun_notu` metninin içinde
    "Büyük Boy" gibi ifadeler arayarak buluyordu; artık NOT FİYATI ETKİLEMEZ,
    yalnızca mutfağa/kasaya gösterilir.
    """

    urun_id: int = Field(gt=0)
    adet: int = Field(gt=0, le=MAX_LINE_QUANTITY)
    birim_fiyat: float = Field(ge=0)
    urun_notu: Optional[str] = Field(default="", max_length=255)
    opsiyon_ids: List[int] = Field(default_factory=list, max_length=MAX_LINE_OPTIONS)

    @field_validator("opsiyon_ids")
    @classmethod
    def validate_opsiyon_ids(cls, value: List[int]) -> List[int]:
        """Pozitif ve tekrarsız kimlikler.

        Aynı kimliğin iki kez gönderilmesi sessizce tekilleştirilmez, reddedilir:
        istemcinin ne istediği belirsizse fiyat da belirsizdir.
        """
        if any(v <= 0 for v in value):
            raise ValueError("Geçersiz opsiyon kimliği.")
        if len(set(value)) != len(value):
            raise ValueError("Aynı opsiyon birden fazla kez gönderilemez.")
        return value


class SiparisOlusturModel(BaseModel):
    """`POST /api/siparisler` gövdesi.

    `customer_session_id` alanı bilinçli olarak yoktur: siparişin sahibi
    controller tarafından doğrulanmış oturumdan yazılır, istemci "bu sipariş şu
    kişinin" diye bir iddiada bulunamaz.
    """

    masa_id: int = Field(gt=0)
    toplam_tutar: float = Field(ge=0)
    odeme_yontemi: PaymentMethod = PaymentMethod.POS
    urunler: List[SiparisItemModel] = Field(min_length=1)
    device_id: Optional[str] = Field(default=None, max_length=100)
    current_totp_token: Optional[str] = Field(default=None, min_length=1)


class SiparisDuzenleModel(BaseModel):
    """Staff order-edit request.

    ``toplam_tutar`` and each line's ``birim_fiyat`` are advisory only: the
    service recomputes both from the product catalogue. There is deliberately no
    ``garson_adi`` field — the audit name is taken from the authenticated
    principal so it cannot be forged by the caller.
    """

    toplam_tutar: float = Field(ge=0)
    urunler: List[SiparisItemModel] = Field(min_length=1)


class DurumGuncelleModel(BaseModel):
    """Order status transition request.

    ``garson_adi`` is intentionally absent for the same reason as above.
    """

    yeni_durum: Union[OrderStatus, OrderAction]
    pin_code: Optional[str] = Field(default=None, max_length=255)

    @field_validator("yeni_durum", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        """Metin durumları normalize eder; diğer tipleri olduğu gibi geçirir.

        Doğrulama işini pydantic'in enum çözümlemesine bırakır: buradaki tek iş,
        " Hazir " gibi bir girdinin `hazir` ile eşleşmesini sağlamak.
        """
        if isinstance(value, str):
            return value.strip().lower()
        return value
