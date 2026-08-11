from typing import List, Optional, Union

from pydantic import BaseModel, Field, field_validator

from app.enums import OrderAction, OrderStatus, PaymentMethod, PaymentStatus


class SiparisItemModel(BaseModel):
    urun_id: int = Field(gt=0)
    adet: int = Field(gt=0)
    birim_fiyat: float = Field(ge=0)
    urun_notu: Optional[str] = Field(default="", max_length=255)


class SiparisOlusturModel(BaseModel):
    masa_id: int = Field(gt=0)
    toplam_tutar: float = Field(ge=0)
    odeme_yontemi: PaymentMethod = PaymentMethod.POS
    urunler: List[SiparisItemModel] = Field(min_length=1)
    device_id: Optional[str] = Field(default=None, max_length=100)
    current_totp_token: Optional[str] = Field(default=None, min_length=1)


class SiparisDuzenleModel(BaseModel):
    toplam_tutar: float = Field(ge=0)
    urunler: List[SiparisItemModel] = Field(min_length=1)
    garson_adi: Optional[str] = Field(default=None, max_length=100)


class DurumGuncelleModel(BaseModel):
    yeni_durum: Union[OrderStatus, OrderAction]
    garson_adi: Optional[str] = Field(default=None, max_length=100)
    pin_code: Optional[str] = Field(default=None, max_length=255)

    @field_validator("yeni_durum", mode="before")
    @classmethod
    def normalize_status(cls, value):
        if isinstance(value, str):
            return value.strip().lower()
        return value


class SiparisDetayResponse(BaseModel):
    urun_id: int
    urun_adi: str
    adet: int
    birim_fiyat: float
    urun_notu: str
    ara_toplam: float


class SiparisResponse(BaseModel):
    id: int
    masa_id: int
    masa_no: str
    siparis_kodu: str
    toplam_tutar: float
    odeme_yontemi: Optional[PaymentMethod] = None
    odeme_durumu: PaymentStatus
    siparis_durumu: OrderStatus
    olusturma_tarihi: Optional[str] = None
    garson_adi: Optional[str] = None
    device_id: Optional[str] = None
    detaylar: List[SiparisDetayResponse] = Field(default_factory=list)


class SiparisDurumResponse(BaseModel):
    siparis_id: int
    masa_id: int
    masa_no: str
    yeni_durum: OrderStatus
    odeme_durumu: PaymentStatus
    garson_adi: Optional[str] = None
    guncelleme_tarihi: str
    siparis: SiparisResponse


class SiparisIslemCevapModel(BaseModel):
    status: str
    message: str
    siparis: SiparisResponse


class SiparisDurumIslemCevapModel(BaseModel):
    status: str
    message: str
    data: SiparisDurumResponse
