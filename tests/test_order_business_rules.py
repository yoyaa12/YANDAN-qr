import unittest
from unittest.mock import MagicMock
from fastapi import HTTPException

from app.enums import OrderAction, OrderStatus, PaymentMethod
from app.schemas.orders import SiparisItemModel, SiparisOlusturModel
from app.services.order_authorization import validate_order_state_transition
from app.services.siparis_service import SiparisService


class TestOrderBusinessRules(unittest.TestCase):

    def setUp(self):
        self.mock_siparis_repo = MagicMock()
        self.mock_masa_repo = MagicMock()
        self.mock_urun_repo = MagicMock()
        self.mock_auth_repo = MagicMock()
        self.service = SiparisService(
            siparis_repo=self.mock_siparis_repo,
            masa_repo=self.mock_masa_repo,
            urun_repo=self.mock_urun_repo,
            auth_repo=self.mock_auth_repo,
        )

    def test_price_recalculation_and_underpay_rejection(self):
        db_product = {"id": 1, "urun_adi": "Köfte", "fiyat": 100.0, "stok_miktari": 50, "aktif_mi": True}
        self.mock_urun_repo.get_by_id.return_value = db_product

        item = SiparisItemModel(urun_id=1, adet=2, birim_fiyat=10.0, urun_notu="")

        with self.assertRaises(HTTPException) as ctx:
            self.service._calculate_item_authoritative_price(db_product, item)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("düşük olamaz", ctx.exception.detail)

    def test_authoritative_price_with_option_deltas(self):
        db_product = {"id": 2, "urun_adi": "Karışık Pizza", "fiyat": 150.0, "stok_miktari": 20, "aktif_mi": True}
        self.mock_urun_repo.get_by_id.return_value = db_product

        item = SiparisItemModel(urun_id=2, adet=1, birim_fiyat=190.0, urun_notu="Orta Boy, 🥤 Pipetli Olsun")
        unit_price, line_total = self.service._calculate_item_authoritative_price(db_product, item)

        self.assertEqual(unit_price, 190.0) # 150 + 40
        self.assertEqual(line_total, 190.0)

    def test_inactive_product_rejection(self):
        db_product = {"id": 3, "urun_adi": "Eski Çorba", "fiyat": 50.0, "stok_miktari": 10, "aktif_mi": False}
        self.mock_urun_repo.get_by_id.return_value = db_product

        data = SiparisOlusturModel(
            masa_id=1,
            toplam_tutar=50.0,
            odeme_yontemi=PaymentMethod.POS,
            urunler=[SiparisItemModel(urun_id=3, adet=1, birim_fiyat=50.0, urun_notu="")],
        )

        with self.assertRaises(HTTPException) as ctx:
            self.service._process_order_items(101, data.urunler)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("satışa kapalıdır", ctx.exception.detail)

    def test_insufficient_stock_rejection(self):
        db_product = {"id": 4, "urun_adi": "Kızarmış Patates", "fiyat": 40.0, "stok_miktari": 2, "aktif_mi": True}
        self.mock_urun_repo.get_by_id.return_value = db_product

        data = SiparisOlusturModel(
            masa_id=1,
            toplam_tutar=200.0,
            odeme_yontemi=PaymentMethod.POS,
            urunler=[SiparisItemModel(urun_id=4, adet=5, birim_fiyat=40.0, urun_notu="")],
        )

        with self.assertRaises(HTTPException) as ctx:
            self.service._process_order_items(102, data.urunler)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("yetersiz stok", ctx.exception.detail)

    def test_legal_state_transitions(self):
        try:
            validate_order_state_transition(OrderStatus.PAID_IN_KITCHEN.value, OrderStatus.PREPARING)
            validate_order_state_transition(OrderStatus.PREPARING.value, OrderStatus.READY)
            validate_order_state_transition(OrderStatus.READY.value, OrderStatus.DELIVERED)
            validate_order_state_transition(OrderStatus.DELIVERED.value, OrderStatus.PAID_CLOSED)
        except HTTPException:
            self.fail("Legal state transitions raised unexpected HTTPException!")

    def test_illegal_state_transitions(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_order_state_transition(OrderStatus.DELIVERED.value, OrderStatus.PREPARING)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("durumuna geçirilemez", ctx.exception.detail)

        with self.assertRaises(HTTPException) as ctx2:
            validate_order_state_transition(OrderStatus.CANCELLED.value, OrderStatus.READY)
        self.assertEqual(ctx2.exception.status_code, 400)
        self.assertIn("sonlandırılmış durumdadır", ctx2.exception.detail)


if __name__ == "__main__":
    unittest.main()
