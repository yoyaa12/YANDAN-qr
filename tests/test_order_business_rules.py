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
            self.service._calculate_item_authoritative_price(db_product, item, [])

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("dusuk olamaz", ctx.exception.detail)

    def test_authoritative_price_with_option_deltas(self):
        """Fiyat farki KATALOG satirindan gelir, notun icinden degil."""
        db_product = {"id": 2, "urun_adi": "Karışık Pizza", "fiyat": 150.0, "stok_miktari": 20, "aktif_mi": True}
        self.mock_urun_repo.get_by_id.return_value = db_product

        orta_boy = {"id": 2, "grup": "boy", "kod": "medium", "ad": "Orta Boy",
                    "fiyat_farki": 40.0, "fiyat_carpani": None}
        item = SiparisItemModel(
            urun_id=2, adet=1, birim_fiyat=190.0,
            urun_notu="🥤 Pipetli Olsun", opsiyon_ids=[2],
        )
        unit_price, line_total = self.service._calculate_item_authoritative_price(
            db_product, item, [orta_boy]
        )

        self.assertEqual(unit_price, 190.0)  # 150 + 40
        self.assertEqual(line_total, 190.0)

    def test_a_note_can_no_longer_change_the_price(self):
        """Asil regresyon: musterinin serbest metni fiyati etkilemez.

        Onceden `"Orta Boy" in urun_notu` kontrolu vardi; not kutusuna o
        ifadeyi yazan biri fiyati degistirebiliyordu. Artik fiyat yalnizca
        secilen opsiyon kimliklerinden turetilir.
        """
        db_product = {"id": 2, "urun_adi": "Karışık Pizza", "fiyat": 150.0, "stok_miktari": 20, "aktif_mi": True}

        for kurcalanmis_not in (
            "Orta Boy",
            "En Büyük Boy lütfen",
            "1.5 Porsiyon olsun",
            "Ekstra Manda Kaymağı istiyorum",
            "En Büyük Boy En Büyük Boy En Büyük Boy",
        ):
            with self.subTest(not_metni=kurcalanmis_not):
                item = SiparisItemModel(
                    urun_id=2, adet=1, birim_fiyat=150.0, urun_notu=kurcalanmis_not
                )
                unit_price, _ = self.service._calculate_item_authoritative_price(
                    db_product, item, []
                )
                self.assertEqual(unit_price, 150.0, "not fiyati degistirmemeli")

    def test_the_most_expensive_size_is_no_longer_unreachable(self):
        """Eski `elif` zincirinde +140 dali olu koddu.

        "En Buyuk Boy" metni "Buyuk Boy" metnini icerdigi icin zincir ikinci
        dalda kapaniyor, en pahali boy bir alt boyun fiyatina satiliyordu.
        Kimlikle secim bu sinifi hatayi yapisal olarak ortadan kaldirir.
        """
        db_product = {"id": 2, "urun_adi": "Karışık Pizza", "fiyat": 200.0, "stok_miktari": 20, "aktif_mi": True}
        en_buyuk = {"id": 4, "grup": "boy", "kod": "jumbo", "ad": "En Büyük Boy",
                    "fiyat_farki": 140.0, "fiyat_carpani": None}

        item = SiparisItemModel(urun_id=2, adet=1, birim_fiyat=340.0, opsiyon_ids=[4])
        unit_price, _ = self.service._calculate_item_authoritative_price(
            db_product, item, [en_buyuk]
        )

        self.assertEqual(unit_price, 340.0, "200 + 140")

    def test_a_multiplier_option_scales_the_base_price(self):
        db_product = {"id": 5, "urun_adi": "Izgara Köfte", "fiyat": 200.0, "stok_miktari": 20, "aktif_mi": True}
        bir_bucuk = {"id": 6, "grup": "porsiyon", "kod": "p1_5", "ad": "1.5 Porsiyon",
                     "fiyat_farki": 0.0, "fiyat_carpani": 1.4}

        item = SiparisItemModel(urun_id=5, adet=2, birim_fiyat=280.0, opsiyon_ids=[6])
        unit_price, line_total = self.service._calculate_item_authoritative_price(
            db_product, item, [bir_bucuk]
        )

        self.assertEqual(unit_price, 280.0)  # 200 * 1.4
        self.assertEqual(line_total, 560.0)

    def test_an_unknown_option_id_is_rejected(self):
        item = SiparisItemModel(urun_id=2, adet=1, birim_fiyat=150.0, opsiyon_ids=[999])

        with self.assertRaises(HTTPException) as ctx:
            self.service._resolve_line_options(item, {})

        self.assertEqual(ctx.exception.status_code, 400)

    def test_two_options_from_the_same_group_are_rejected(self):
        """Notta iki boy birden gectiginde fiyati zincirdeki SIRA belirliyordu."""
        opsiyon_map = {
            2: {"id": 2, "grup": "boy", "ad": "Orta Boy", "fiyat_farki": 40.0, "fiyat_carpani": None},
            3: {"id": 3, "grup": "boy", "ad": "Büyük Boy", "fiyat_farki": 85.0, "fiyat_carpani": None},
        }
        item = SiparisItemModel(urun_id=2, adet=1, birim_fiyat=150.0, opsiyon_ids=[2, 3])

        with self.assertRaises(HTTPException) as ctx:
            self.service._resolve_line_options(item, opsiyon_map)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("yalnizca bir secenek", ctx.exception.detail)

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
            self.service._price_items_authoritatively(data.urunler)

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
            priced, _total = self.service._price_items_authoritatively(data.urunler)
            self.service._assert_stock_available(priced)

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


class TestStateMachineFailsClosed(unittest.TestCase):
    """Haritada olmayan bir durum tüm geçişlere izin vermemelidir.

    Önceki kod `allowed is not None` kontrolü yaptığı için eşlenmemiş tek bir
    durum değeri o sipariş adına durum makinesini tamamen devre dışı bırakıyordu.
    """

    def test_unknown_status_is_rejected_instead_of_allowing_everything(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_order_state_transition("bilinmeyen_durum", OrderStatus.DELIVERED)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("bilinmeyen bir durumda", ctx.exception.detail)

    def test_empty_status_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            validate_order_state_transition("", OrderStatus.PREPARING)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_every_order_status_is_mapped(self):
        """Drift koruması: enum'a yeni durum eklenirse harita da güncellenmeli."""
        from app.services.order_authorization import _ALLOWED_STATE_TRANSITIONS

        unmapped = [s.value for s in OrderStatus if s.value not in _ALLOWED_STATE_TRANSITIONS]
        self.assertEqual(unmapped, [], f"Geçiş haritasında eksik durum(lar): {unmapped}")

    def test_legacy_payment_pending_status_has_legal_transitions(self):
        try:
            validate_order_state_transition(
                OrderStatus.PAYMENT_PENDING.value, OrderStatus.CANCELLED
            )
        except HTTPException:
            self.fail("Legacy 'odeme_bekliyor' durumu iptal edilebilmeli")


class TestIdempotencyCacheEviction(unittest.TestCase):
    """Tekrarlı sipariş önbelleği süresiz büyümemelidir."""

    def setUp(self):
        from app.services import siparis_service
        self.mod = siparis_service
        self._original = dict(siparis_service._RECENT_ORDERS_CACHE)
        self.addCleanup(self._restore)
        siparis_service._RECENT_ORDERS_CACHE.clear()

    def _restore(self):
        self.mod._RECENT_ORDERS_CACHE.clear()
        self.mod._RECENT_ORDERS_CACHE.update(self._original)

    def test_expired_entries_are_dropped(self):
        now = 1_000.0
        window = self.mod._IDEMPOTENCY_WINDOW_SECONDS
        self.mod._RECENT_ORDERS_CACHE["eski"] = (now - window - 1, "yanit")
        self.mod._RECENT_ORDERS_CACHE["yeni"] = (now, "yanit")

        self.mod._prune_idempotency_cache(now)

        self.assertNotIn("eski", self.mod._RECENT_ORDERS_CACHE)
        self.assertIn("yeni", self.mod._RECENT_ORDERS_CACHE)

    def test_cache_is_capped_even_inside_one_window(self):
        now = 1_000.0
        limit = self.mod._IDEMPOTENCY_MAX_ENTRIES
        for i in range(limit + 50):
            # Hepsi taze; yalnizca tavan kurali devreye girmeli.
            self.mod._RECENT_ORDERS_CACHE[f"k{i}"] = (now + i * 0.001, "yanit")

        self.mod._prune_idempotency_cache(now)

        self.assertLessEqual(len(self.mod._RECENT_ORDERS_CACHE), limit)


if __name__ == "__main__":
    unittest.main()
