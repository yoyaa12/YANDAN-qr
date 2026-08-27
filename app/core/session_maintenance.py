"""Müşteri oturumlarının arka plan bakımı.

`CustomerSessions.is_active` bir *iptal* bayrağıdır, *geçerlilik* bayrağı
değildir: yalnızca adisyon kapandığında veya oturum elle iptal edildiğinde
düşer. Süre dolduğunda kimse gidip bayrağı indirmediği için tabloda
"`expires_at` geçmiş ama `is_active = 1`" satırları birikiyordu.

Bu bir güvenlik açığı değildi — `AuthRepository.get_active_customer_session`
iki koşulu birden arar, süresi geçmiş satır bayrağı 1 olsa da kabul edilmez.
Sorun okunabilirlikti: veritabanına bakan kişi ölmüş oturumları canlı sanıyordu.
Bu görev, saklanan durumu gerçekle aynı hizaya getirir.
"""

import asyncio
import logging

from app.database import DatabaseSession
from app.repositories.auth_repo import AuthRepository

logger = logging.getLogger(__name__)

# Oturum ömrü 90 dakika (`AuthService.CUSTOMER_SESSION_TTL_MINUTES`). 15
# dakikalık süpürme, bir satırın en fazla o kadar süre yanlış görünmesi
# demektir; daha sık çalışmak boşuna veritabanı trafiği üretir.
SWEEP_INTERVAL_SECONDS = 15 * 60


def _sweep_blocking() -> int:
    """Süpürmenin veritabanına dokunan, bloklayan kısmı."""
    return AuthRepository(DatabaseSession()).deactivate_expired_customer_sessions()


async def sweep_expired_customer_sessions() -> int:
    """Süresi dolmuş oturumları pasife alır, etkilenen satır sayısını döner.

    `pyodbc` bloklayıcı olduğu için sorgu ayrı bir iş parçacığında çalışır;
    aksi halde event loop bekletilir ve o sırada gelen socket olayları gecikir.
    """
    return await asyncio.to_thread(_sweep_blocking)


async def run_session_maintenance(
    interval_seconds: int = SWEEP_INTERVAL_SECONDS,
) -> None:
    """Süpürmeyi periyodik olarak çalıştırır. Uygulama kapanınca iptal edilir."""
    while True:
        try:
            closed = await sweep_expired_customer_sessions()
            if closed > 0:
                logger.info(
                    "Suresi dolmus %s musteri oturumu pasife alindi.", closed
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Bakım görevi uygulamayı asla düşürmemeli. Veritabanı o an
            # erişilemezse bir sonraki turda yeniden denenir.
            logger.exception("Musteri oturumu bakimi basarisiz oldu.")

        await asyncio.sleep(interval_seconds)
