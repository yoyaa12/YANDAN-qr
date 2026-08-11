from fastapi import APIRouter, Depends
from typing import Optional, List
from app.auth.dependencies import require_roles
from app.auth.models import StaffPrincipal
from app.enums import UserRole
from app.services.siparis_service import SiparisService
from app.schemas.orders import (
    SiparisOlusturModel, 
    DurumGuncelleModel, 
    SiparisDuzenleModel,
    SiparisIslemCevapModel,
    SiparisDurumIslemCevapModel,
    SiparisResponse
)

router = APIRouter()

authenticated_staff = require_roles(
    UserRole.ADMIN,
    UserRole.WAITER,
    UserRole.KITCHEN,
    UserRole.CASHIER,
)
order_editor = require_roles(UserRole.ADMIN, UserRole.WAITER)

from fastapi import HTTPException

@router.post("/siparisler", response_model=SiparisIslemCevapModel)
async def create_siparis(data: SiparisOlusturModel, service: SiparisService = Depends()):
    full_order = await service.create_siparis(data)
    return {"status": "success", "message": "Sipariş oluşturuldu.", "siparis": full_order}

@router.get("/siparisler", response_model=List[SiparisResponse])
async def get_siparisler(
    durum: Optional[str] = None,
    masa_id: Optional[int] = None,
    service: SiparisService = Depends(),
    _principal: StaffPrincipal = Depends(authenticated_staff),
):
    return service.get_siparisler(durum, masa_id)

@router.patch("/siparisler/{siparis_id}/durum", response_model=SiparisDurumIslemCevapModel)
async def update_siparis_durumu(
    siparis_id: int,
    data: DurumGuncelleModel,
    service: SiparisService = Depends(),
    principal: StaffPrincipal = Depends(authenticated_staff),
):
    data = data.model_copy(update={"garson_adi": principal.username})
    event_payload = await service.update_siparis_durumu(siparis_id, data, principal)
    return {"status": "success", "message": "Sipariş güncellendi.", "data": event_payload}

@router.put("/siparisler/{siparis_id}", response_model=SiparisIslemCevapModel)
async def update_siparis_items(
    siparis_id: int,
    data: SiparisDuzenleModel,
    service: SiparisService = Depends(),
    principal: StaffPrincipal = Depends(order_editor),
):
    data = data.model_copy(update={"garson_adi": principal.username})
    updated_order = await service.update_siparis_items(siparis_id, data)
    return {"status": "success", "message": "Sipariş kalemleri güncellendi.", "siparis": updated_order}
