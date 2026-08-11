from typing import Optional

from pydantic import BaseModel, Field

from app.enums import TableStatus


class MasaEkleModel(BaseModel):
    masa_no: str = Field(min_length=1, max_length=20)


class MasaResponse(BaseModel):
    id: int
    masa_no: str
    durum: TableStatus
    secim_durumu: Optional[dict] = None


class MoveMasaModel(BaseModel):
    from_masa_id: int = Field(gt=0)
    to_masa_id: int = Field(gt=0)


class VerifyQRModel(BaseModel):
    token: str = Field(min_length=1)
    device_id: Optional[str] = Field(default=None, max_length=100)


class QRDogrulamaResponse(BaseModel):
    valid: bool
    message: str
    masa_id: Optional[int] = None
    session_token: Optional[str] = None

