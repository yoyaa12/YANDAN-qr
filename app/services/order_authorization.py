from fastapi import HTTPException, status

from app.enums import OrderAction, OrderStatus, UserRole


_ROLE_STATUS_TARGETS: dict[UserRole, frozenset[OrderStatus | OrderAction]] = {
    UserRole.ADMIN: frozenset((*OrderStatus, *OrderAction)),
    UserRole.WAITER: frozenset(
        {
            OrderStatus.WAITER_APPROVED_IN_KITCHEN,
            OrderAction.CASH_COLLECTED,
            OrderStatus.DELIVERED,
        }
    ),
    UserRole.KITCHEN: frozenset(
        {
            OrderStatus.PREPARING,
            OrderStatus.READY,
        }
    ),
    UserRole.CASHIER: frozenset({OrderAction.CASH_COLLECTED}),
}


def enforce_order_status_role(
    role: UserRole,
    requested_status: OrderStatus | OrderAction,
) -> None:
    """Enforce the staff-role boundary before an order mutation reaches the DB."""
    allowed_targets = _ROLE_STATUS_TARGETS.get(role, frozenset())
    if requested_status not in allowed_targets:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Personel rolünüz bu sipariş durum işlemi için yetkili değil.",
        )
