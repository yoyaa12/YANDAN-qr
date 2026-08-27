"""Masa taşımada oturum sürekliliği ve kasa tahsilatının siparişlere işlenmesi.

İki ayrı kusur, ikisi de aynı eksik bağdan doğuyordu: adisyonun parçaları
birbirinden kopuk taşınıyor / kapanıyordu.

1. `move_masa` yalnızca `Siparisler.masa_id` güncelliyordu. Müşteri oturumları
   kaynak masada kalıyor, bunun iki görünür sonucu oluyordu:
   - Müşteri hedef masada kendi siparişlerini göremiyordu. `is_mine` hesabı
     `Siparisler.customer_session_id` eşitliğine dayanır; istemci hedef masa
     için yeni oturum açmak zorunda kaldığında taşınmış siparişler eski satırın
     kimliğini taşımaya devam ediyordu.
   - Adisyon hedef masada kapatıldığında `revoke_all_sessions_for_masa` kaynak
     masada unutulan satırları görmüyor, o satırlar TTL dolana kadar
     `is_active = 1` kalıyordu.

2. `add_tahsilat` yalnızca `MasaTahsilatlari`'ya satır yazıyordu. Kasada alınan
   para siparişlerin `odeme_durumu` alanına hiç yansımıyordu: hesap toplamı ile
   ödenen tutar birbirini tutarken adisyon satırları "Açık" görünmeye devam
   ediyordu.

İkinci düzeltmenin kendi tuzağı var ve bu dosyadaki en önemli test onu tutuyor:
karşılanan tahsilat satırları kapatılmazsa aynı para hem "kasada tahsil edilen"
hem "ödenmiş sipariş" olarak iki kez sayılır ve masaya sonradan eklenen sipariş
hiç ödeme alınmadan ödenmiş görünür.
"""

import asyncio
import contextlib
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.enums import OrderStatus, PaymentStatus, TableStatus
from app.services import siparis_service as siparis_service_module
from app.services.siparis_service import SiparisService


@contextlib.contextmanager
def _no_transaction():
    yield None


class _FakeSiparisRepo:
    """Sipariş ve tahsilat satırlarını bellekte tutan sahte repository.

    `MagicMock` burada yetmez: bu dosyadaki asıl soru "aynı para iki kez
    sayılıyor mu" ve buna ancak durumu gerçekten güncelleyen bir taklit cevap
    verebilir. Sorgu mantığı üretimdeki SQL'in koşullarıyla birebir aynıdır.
    """

    def __init__(self):
        self.orders = []
        self.tahsilatlar = []

    def _live_open_orders(self, masa_id):
        return [
            o
            for o in self.orders
            if o["masa_id"] == masa_id
            and o["odeme_durumu"] != PaymentStatus.PAID.value
            and o["siparis_durumu"]
            not in (OrderStatus.CANCELLED.value, OrderStatus.PAID_CLOSED.value)
        ]

    def add_order(self, masa_id, tutar, odeme_durumu=PaymentStatus.PENDING.value):
        self.orders.append(
            {
                "id": len(self.orders) + 1,
                "masa_id": masa_id,
                "toplam_tutar": tutar,
                "odeme_durumu": odeme_durumu,
                "siparis_durumu": OrderStatus.WAITER_APPROVED_IN_KITCHEN.value,
            }
        )

    # --- üretimdeki repository arayüzü ---

    def add_masa_tahsilat(self, masa_id, tutar, odeme_yontemi):
        self.tahsilatlar.append(
            {"masa_id": masa_id, "tutar": tutar, "odeme_yontemi": odeme_yontemi, "is_closed": False}
        )

    def get_masa_tahsilat_toplami(self, masa_id):
        return float(
            sum(t["tutar"] for t in self.tahsilatlar if t["masa_id"] == masa_id and not t["is_closed"])
        )

    def close_tahsilatlar_for_masa(self, masa_id):
        for t in self.tahsilatlar:
            if t["masa_id"] == masa_id:
                t["is_closed"] = True

    def get_open_orders_total_for_masa(self, masa_id):
        return float(sum(o["toplam_tutar"] for o in self._live_open_orders(masa_id)))

    def mark_open_orders_paid_for_masa(self, masa_id):
        acik = self._live_open_orders(masa_id)
        for o in acik:
            o["odeme_durumu"] = PaymentStatus.PAID.value
        return len(acik)


def _build_service(siparis_repo=None):
    return SiparisService(
        siparis_repo=siparis_repo if siparis_repo is not None else MagicMock(),
        masa_repo=MagicMock(),
        urun_repo=MagicMock(),
        auth_repo=MagicMock(),
    )


class _ServiceTestCase(unittest.TestCase):
    """`db_transaction` ve `event_bus` her testte devre dışı bırakılır."""

    def setUp(self):
        siparis_service_module.TABLE_MOVES_MAP.clear()
        self.addCleanup(siparis_service_module.TABLE_MOVES_MAP.clear)

        patcher_tx = patch("app.services.siparis_service.db_transaction", _no_transaction)
        patcher_bus = patch("app.services.siparis_service.event_bus")
        patcher_browsing = patch("app.services.siparis_service.clear_browsing_table")
        for patcher in (patcher_tx, patcher_bus, patcher_browsing):
            self.addCleanup(patcher.stop)
        patcher_tx.start()
        patcher_bus.start().publish = AsyncMock()
        patcher_browsing.start()


class TableMoveCarriesSessionsTests(_ServiceTestCase):
    """Adisyon taşınırken müşteri oturumları da taşınmalıdır."""

    def setUp(self):
        super().setUp()
        self.service = _build_service()
        self.service.masa_repo.get_by_id.side_effect = lambda mid: {
            "id": mid,
            "masa_no": f"S-{mid}",
        }

    def _move(self, from_id=5, to_id=6):
        asyncio.run(self.service.move_masa(from_id, to_id))

    def test_active_sessions_follow_the_orders(self):
        """Asıl regresyon: siparişler taşınıyor, oturumlar geride kalıyordu."""
        self._move()

        self.service.auth_repo.move_active_sessions_to_masa.assert_called_once_with(5, 6)

    def test_orders_and_sessions_move_to_the_same_table(self):
        self._move()

        self.service.siparis_repo.move_orders_between_masalar.assert_called_once_with(5, 6)
        move_call = self.service.auth_repo.move_active_sessions_to_masa.call_args
        self.assertEqual(move_call.args, (5, 6))

    def test_the_source_table_is_emptied_and_the_target_occupied(self):
        self._move()

        self.service.masa_repo.update_durum.assert_any_call(6, TableStatus.OCCUPIED.value)
        self.service.masa_repo.update_durum.assert_any_call(5, TableStatus.EMPTY.value)

    def test_closing_the_moved_check_now_reaches_every_session(self):
        """Taşıma sonrası kapanış: süpürge artık hedef masada hepsini bulur.

        Oturumlar taşınmadığında `revoke_all_sessions_for_masa(6)` kaynak
        masada kalan satırları görmüyordu. Taşıma ve kapanış aynı masa
        kimliğini kullandığı sürece geride satır kalamaz.
        """
        self._move(5, 6)
        self.service.siparis_repo.get_undelivered_details_for_masa.return_value = []

        asyncio.run(self.service.clear_masa(6))

        moved_to = self.service.auth_repo.move_active_sessions_to_masa.call_args.args[1]
        revoked = self.service.auth_repo.revoke_all_sessions_for_masa.call_args.args[0]
        self.assertEqual(moved_to, revoked)


class TillSettlementTests(_ServiceTestCase):
    """Kasada alınan para siparişlerin ödeme durumuna yansımalıdır."""

    def setUp(self):
        super().setUp()
        self.repo = _FakeSiparisRepo()
        self.service = _build_service(self.repo)

    def _collect(self, masa_id, tutar):
        return asyncio.run(self.service.add_tahsilat(masa_id, tutar, "Nakit"))

    def test_a_covered_check_marks_its_orders_paid(self):
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(5, 90.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)

    def test_a_partial_payment_leaves_the_orders_open(self):
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(5, 40.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 90.0)
        self.assertEqual(self.repo.get_masa_tahsilat_toplami(5), 40.0)

    def test_partial_payments_that_add_up_settle_the_check(self):
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(5, 40.0)
        self._collect(5, 50.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)

    def test_an_order_paid_at_ordering_time_is_not_charged_again(self):
        """POS ile ödenmiş sipariş kasanın karşılaması gereken tutara girmez."""
        self.repo.add_order(masa_id=5, tutar=90.0, odeme_durumu=PaymentStatus.PAID.value)
        self.repo.add_order(masa_id=5, tutar=90.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 90.0)
        self._collect(5, 90.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)

    def test_settled_collections_are_closed_so_the_money_is_counted_once(self):
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(5, 90.0)

        self.assertEqual(
            self.repo.get_masa_tahsilat_toplami(5),
            0.0,
            "siparişlere işlenen tahsilat açık kalırsa aynı para iki kez sayılır",
        )

    def test_a_later_order_is_not_marked_paid_by_already_spent_money(self):
        """Bu düzeltmenin tuzağı: kapatılmayan tahsilat bedava sipariş üretir.

        Adisyon kapanmadan masaya yeni sipariş eklenebiliyor. Karşılanan
        tahsilat satırları kapatılmasaydı o eski para yeni siparişi de
        karşılıyor sanılır, kasa hiç ödeme almadan siparişi ödenmiş yapardı.
        """
        self.repo.add_order(masa_id=5, tutar=90.0)
        self._collect(5, 90.0)

        self.repo.add_order(masa_id=5, tutar=50.0)

        self.assertEqual(
            self.repo.get_open_orders_total_for_masa(5),
            50.0,
            "yeni sipariş eski parayla ödenmiş sayılamaz",
        )

    def test_the_later_order_settles_on_its_own_payment(self):
        self.repo.add_order(masa_id=5, tutar=90.0)
        self._collect(5, 90.0)
        self.repo.add_order(masa_id=5, tutar=50.0)

        self._collect(5, 50.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)

    def test_a_partial_payment_on_the_later_order_does_not_settle_it(self):
        self.repo.add_order(masa_id=5, tutar=90.0)
        self._collect(5, 90.0)
        self.repo.add_order(masa_id=5, tutar=50.0)

        self._collect(5, 20.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 50.0)
        self.assertEqual(self.repo.get_masa_tahsilat_toplami(5), 20.0)

    def test_collecting_on_a_table_with_no_open_orders_changes_nothing(self):
        self.repo.add_order(masa_id=5, tutar=90.0, odeme_durumu=PaymentStatus.PAID.value)

        self._collect(5, 25.0)

        self.assertEqual(
            self.repo.get_masa_tahsilat_toplami(5),
            25.0,
            "karşılayacak açık sipariş yokken tahsilat kapatılmamalı",
        )

    def test_a_cancelled_order_is_not_part_of_the_amount_due(self):
        self.repo.add_order(masa_id=5, tutar=90.0)
        self.repo.orders[0]["siparis_durumu"] = OrderStatus.CANCELLED.value
        self.repo.add_order(masa_id=5, tutar=40.0)

        self._collect(5, 40.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)

    def test_another_tables_collection_does_not_settle_this_table(self):
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(6, 90.0)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 90.0)

    def test_sub_kurus_rounding_does_not_block_settlement(self):
        """`DECIMAL(10,2)` -> float dönüşümü kuruş altı fark üretebiliyor."""
        self.repo.add_order(masa_id=5, tutar=90.0)

        self._collect(5, 89.999)

        self.assertEqual(self.repo.get_open_orders_total_for_masa(5), 0.0)


if __name__ == "__main__":
    unittest.main()
