from typing import Optional

from pydantic import BaseModel, Field

from app.enums import UserRole


class GarsonPinVerifyModel(BaseModel):
    pin_code: str = Field(min_length=1, max_length=255)


class LoginModel(BaseModel):
    kullanici_adi: str = Field(min_length=1, max_length=50)
    sifre: str = Field(min_length=1, max_length=255)


class BanDeviceModel(BaseModel):
    device_id: str = Field(min_length=1, max_length=100)


class KullaniciResponse(BaseModel):
    id: int
    kullanici_adi: Optional[str] = None
    garson_adi: Optional[str] = None
    rol: Optional[UserRole] = None


class GarsonResponse(BaseModel):
    id: int
    ad_soyad: str


class LoginResponse(BaseModel):
    status: str
    user: KullaniciResponse


class GarsonPinResponse(BaseModel):
    status: str
    garson: KullaniciResponse
