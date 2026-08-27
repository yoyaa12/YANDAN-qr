from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import get_current_customer, get_current_staff
from app.auth.models import StaffPrincipal
from app.services.auth_service import AuthService
from app.schemas.auth import (
    KullaniciResponse,
    LoginModel,
    LoginResponse,
    MusteriOturumResponse,
)

router = APIRouter()

@router.post("/auth/login", response_model=LoginResponse)
def login(
    data: LoginModel, request: Request, service: AuthService = Depends()
) -> LoginResponse:
    client_host = request.client.host if request.client else "unknown"
    result = service.login(data, client_host)
    user = KullaniciResponse(
        id=result.principal.user_id,
        kullanici_adi=result.principal.username,
        rol=result.principal.role,
    )
    return LoginResponse(
        status="success",
        user=user,
        access_token=result.access_token,
        expires_in=result.expires_in,
    )


@router.get("/auth/me", response_model=KullaniciResponse)
def get_authenticated_staff(
    principal: StaffPrincipal = Depends(get_current_staff),
) -> KullaniciResponse:
    return KullaniciResponse(
        id=principal.user_id,
        kullanici_adi=principal.username,
        rol=principal.role,
    )


@router.get("/auth/musteri/oturum", response_model=MusteriOturumResponse)
def get_authenticated_customer_session(
    session: dict = Depends(get_current_customer),
) -> MusteriOturumResponse:
    """Müşteri oturumunun hâlâ geçerli olduğunu doğrular.

    Müşteri menüsü sayfa açılışında bunu çağırır. Daha önce böyle bir yol
    yoktu: sayfa, elinde 90 dakikalık geçerli bir oturum olsa bile URL'deki 30
    saniyelik QR kodunu yeniden doğrulatmaya çalışıyordu. Kod çoktan
    süresini doldurduğu için masada oturan müşteri sayfayı yenilediğinde
    "Erişim Reddedildi" duvarına çarpıyordu.

    Bu uç yeni bir yetki vermez: `get_current_customer` zaten sipariş
    yollarının kullandığı doğrulamanın aynısıdır. Geçersiz veya süresi dolmuş
    oturum 401 alır, yani istemcinin `localStorage`'a yazacağı uydurma bir
    değer kapıyı açmaz.
    """
    return MusteriOturumResponse(masa_id=int(session["masa_id"]))
