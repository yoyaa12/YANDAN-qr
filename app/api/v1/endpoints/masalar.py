from fastapi import APIRouter, Depends
from app.services.masa_service import MasaService
from app.services.siparis_service import SiparisService
from app.core.socket_manager import get_browsing_tables

router = APIRouter()
from app.database import DatabaseSession, get_db

from typing import List
from app.schemas.schemas import MasaResponse

@router.get("/masalar", response_model=List[MasaResponse])
async def get_masalar(service: MasaService = Depends()):
    masalar = service.get_masalar()
    browsing = get_browsing_tables()
    for m in masalar:
        m.secim_durumu = browsing.get(m.id)
    return masalar

@router.get("/masalar/{masa_id}/aktif-siparis")
async def get_masa_aktif_siparis(masa_id: int, siparis_service: SiparisService = Depends()):
    return siparis_service.get_masa_aktif_siparis(masa_id)

from pydantic import BaseModel

class MoveMasaModel(BaseModel):
    from_masa_id: int
    to_masa_id: int

@router.post("/masalar/move")
async def move_masa(data: MoveMasaModel, siparis_service: SiparisService = Depends()):
    await siparis_service.move_masa(data.from_masa_id, data.to_masa_id)
    return {"status": "success", "message": "Masa adisyonu başarıyla taşındı."}

@router.post("/masalar/{masa_id}/clear")
async def clear_masa(masa_id: int, siparis_service: SiparisService = Depends()):
    await siparis_service.clear_masa(masa_id)
    return {"status": "success", "message": "Masa oturumu sonlandırıldı."}

class VerifyQRModel(BaseModel):
    token: str
    device_id: str = None

@router.get("/masalar/all-dynamic-qrs")
async def get_all_dynamic_qrs(masa_service: MasaService = Depends()):
    """Tüm masaların canlı 30 saniyelik Dinamik QR verilerini döner."""
    return masa_service.get_all_dynamic_qrs()

@router.get("/masalar/{masa_id}/dynamic-qr")
async def get_dynamic_qr(masa_id: int, masa_service: MasaService = Depends()):
    """Masadaki dijital ekran veya Kasa simülatörü için canlı Dinamik QR bilgisini döner."""
    return masa_service.get_dynamic_qr_info(masa_id)

@router.post("/masalar/{masa_id}/verify-qr")
async def verify_dynamic_qr(masa_id: int, data: VerifyQRModel, masa_service: MasaService = Depends(), siparis_service: SiparisService = Depends()):
    """Müşteri QR okuttuğunda gönderdiği dynamic token'ı doğrular."""
    # 1. Önce cihazın masada halihazırda onaylı bir siparişi var mı ona bakalım
    if data.device_id:
        aktif_siparisler_res = siparis_service.get_masa_aktif_siparis(masa_id)
        if aktif_siparisler_res.get('has_active'):
            # Masada aktif sipariş var (Masa DOLU)
            siparisler = aktif_siparisler_res.get('siparisler', [])
            if any(s.get('device_id') == data.device_id for s in siparisler):
                return {
                    "valid": True,
                    "message": "Cihazınız masada kayıtlı, doğrudan giriş yapıldı.",
                    "masa_id": masa_id
                }

    # 2. Eğer cihaz onaylı değilse normal TOTP doğrulaması yap
    is_valid = masa_service.verify_dynamic_qr_token(masa_id, data.token)
    if is_valid:
        return {
            "valid": True,
            "message": "Dinamik QR başarıyla doğrulandı.",
            "masa_id": masa_id
        }
        
    return {
        "valid": False,
        "message": "Geçersiz veya süresi dolmuş QR kodu! Lütfen masadaki güncel QR kodunu tekrar okutunuz."
    }


