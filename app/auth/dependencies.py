from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import StaffPrincipal
from app.auth.tokens import (
    AuthConfigurationError,
    TokenValidationError,
    decode_access_token,
)
from app.enums import TokenType, UserRole
from app.repositories.auth_repo import AuthRepository


staff_bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="Kısa ömürlü, imzalı STAFF access token",
    scheme_name="StaffBearer",
)


def _unauthorized(detail: str = "Geçerli personel tokenı gerekli.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_staff(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(staff_bearer),
    ],
    repo: Annotated[AuthRepository, Depends()],
) -> StaffPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()

    try:
        claims = decode_access_token(
            credentials.credentials,
            expected_type=TokenType.STAFF,
        )
    except AuthConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kimlik doğrulama yapılandırması hazır değil.",
        ) from exc
    except TokenValidationError as exc:
        raise _unauthorized("Personel tokenı geçersiz veya süresi dolmuş.") from exc

    user = repo.get_staff_by_id(claims.subject)
    if not user:
        raise _unauthorized("Personel tokenı artık geçerli değil.")
    try:
        database_role = UserRole(user["rol"])
        username = str(user["kullanici_adi"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _unauthorized("Personel tokenı artık geçerli değil.") from exc
    if database_role is not claims.role:
        raise _unauthorized("Personel tokenı artık geçerli değil.")

    return StaffPrincipal(
        user_id=claims.subject,
        username=username,
        role=database_role,
    )


def require_roles(*allowed_roles: UserRole) -> Callable[..., StaffPrincipal]:
    if not allowed_roles:
        raise ValueError("At least one role is required")
    allowed = frozenset(
        role if isinstance(role, UserRole) else UserRole(role)
        for role in allowed_roles
    )

    def role_guard(
        principal: Annotated[StaffPrincipal, Depends(get_current_staff)],
    ) -> StaffPrincipal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bu işlem için personel rolünüz yetkili değil.",
            )
        return principal

    return role_guard
