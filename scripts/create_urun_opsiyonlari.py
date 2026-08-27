"""`UrunOpsiyonlari` ve `SiparisDetayOpsiyonlari` tablolarini olusturur ve doldurur.

NEDEN GEREKLI

Opsiyon fiyat farklari (pizza boyu, porsiyon carpani, ekstra malzeme) bugune
kadar HICBIR TABLODA yoktu. Ayni sayilar iki ayri yerde, iki ayri dilde elle
yazilmisti:

    static/js/app.js           PIZZA_SIZES / PORTION_OPTIONS / DESSERT_EXTRAS
    app/services/siparis_service.py   "Buyuk Boy" in note -> += 85.0

Aralarindaki tek bag Turkce bir yaziydi. Sunucu, guvendigi kaynakta (katalog)
opsiyon bulamadigi icin fiyati musterinin serbest metninden turetmek zorunda
kaliyordu. Bunun uc somut sonucu vardi:

  1. "En Buyuk Boy" metni "Buyuk Boy" metnini icerdigi icin `elif` zinciri en
     pahali dala hicbir girdiyle ulasamiyordu: +140 satiri olu koddu ve en
     pahali boy bir alt boyun fiyatina satiliyordu.
  2. Notta iki boy birden geciyorsa fiyati zincirdeki SIRA belirliyordu.
  3. Musterinin not kutusuna yazdigi metin fiyati degistirebiliyordu.

Bu betikten sonra fiyat farki tek bir yerde durur: veritabani.

CALISTIRMA

    .venv/Scripts/python.exe scripts/create_urun_opsiyonlari.py

Betik idempotenttir: tablolar ve satirlar zaten varsa hicbir sey yapmaz,
tekrar tekrar calistirilabilir. Var olan satirlarin fiyatlarini EZMEZ; fiyat
degisikligi isletmenin karari, betigin degil.

GERI ALMA

    .venv/Scripts/python.exe scripts/create_urun_opsiyonlari.py --rollback

Once `SiparisDetayOpsiyonlari`, sonra `UrunOpsiyonlari` silinir (foreign key
sirasi). Siparis kayitlarina dokunulmaz.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import get_db_connection


CREATE_URUN_OPSIYONLARI = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='UrunOpsiyonlari' AND xtype='U')
BEGIN
    CREATE TABLE UrunOpsiyonlari (
        id            INT IDENTITY(1,1) PRIMARY KEY,
        grup          NVARCHAR(20)  NOT NULL,
        kod           NVARCHAR(30)  NOT NULL,
        ad            NVARCHAR(100) NOT NULL,
        aciklama      NVARCHAR(200) NULL,
        fiyat_farki   DECIMAL(10,2) NOT NULL CONSTRAINT DF_UrunOpsiyonlari_fark DEFAULT (0),
        fiyat_carpani DECIMAL(6,3)  NULL,
        siralama      INT           NOT NULL CONSTRAINT DF_UrunOpsiyonlari_sira DEFAULT (0),
        aktif_mi      BIT           NOT NULL CONSTRAINT DF_UrunOpsiyonlari_aktif DEFAULT (1),

        CONSTRAINT UQ_UrunOpsiyonlari_kod UNIQUE (kod),
        CONSTRAINT UQ_UrunOpsiyonlari_grup_ad UNIQUE (grup, ad),

        -- Kurallar uygulama katmaninda da var; burada olmalari, veritabanina
        -- baska bir yoldan (elle SQL, betik) yazan birinin gecersiz satir
        -- birakmasini engeller.
        CONSTRAINT CK_UrunOpsiyonlari_grup
            CHECK (grup IN ('boy', 'porsiyon', 'ekstra')),
        CONSTRAINT CK_UrunOpsiyonlari_fark_pozitif
            CHECK (fiyat_farki >= 0),
        CONSTRAINT CK_UrunOpsiyonlari_carpan_pozitif
            CHECK (fiyat_carpani IS NULL OR fiyat_carpani > 0),
        -- Bir opsiyon ya taban fiyata EKLER ya da onu CARPAR; ikisini birden
        -- yapan bir satir fiyatin nasil hesaplandigini belirsiz birakirdi.
        CONSTRAINT CK_UrunOpsiyonlari_tek_mekanizma
            CHECK (fiyat_carpani IS NULL OR fiyat_farki = 0)
    );
    PRINT 'UrunOpsiyonlari tablosu olusturuldu.';
END
ELSE
    PRINT 'UrunOpsiyonlari tablosu zaten mevcut.';
"""

CREATE_SIPARIS_DETAY_OPSIYONLARI = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='SiparisDetayOpsiyonlari' AND xtype='U')
BEGIN
    CREATE TABLE SiparisDetayOpsiyonlari (
        id               INT IDENTITY(1,1) PRIMARY KEY,
        siparis_detay_id INT NOT NULL,
        opsiyon_id       INT NOT NULL,

        CONSTRAINT FK_SiparisDetayOpsiyon_detay
            FOREIGN KEY (siparis_detay_id) REFERENCES SiparisDetaylari(id),
        CONSTRAINT FK_SiparisDetayOpsiyon_opsiyon
            FOREIGN KEY (opsiyon_id) REFERENCES UrunOpsiyonlari(id),
        CONSTRAINT UQ_SiparisDetayOpsiyon UNIQUE (siparis_detay_id, opsiyon_id)
    );
    PRINT 'SiparisDetayOpsiyonlari tablosu olusturuldu.';
END
ELSE
    PRINT 'SiparisDetayOpsiyonlari tablosu zaten mevcut.';
"""

# Degerler `static/js/app.js` icindeki eski sabit listelerden birebir alindi:
# PIZZA_SIZES (satir 90), PORTION_OPTIONS (satir 114), DESSERT_EXTRAS (satir 147).
# Bu betikten sonra o listeler kaynak olmaktan cikar; tek kaynak burasidir.
#
# (grup, kod, ad, aciklama, fiyat_farki, fiyat_carpani, siralama)
OPSIYONLAR = [
    ("boy", "small",  "Küçük Boy",         "20 cm • 1 Kişilik",                       0.00, None, 1),
    ("boy", "medium", "Orta Boy",          "26 cm • 1-2 Kişilik (🎁 Hediye İçecekli)", 40.00, None, 2),
    ("boy", "large",  "Büyük Boy",         "32 cm • 2-3 Kişilik",                     85.00, None, 3),
    ("boy", "jumbo",  "En Büyük Boy",      "40 cm • 3-4 Kişilik (🎁 Hediye İçecekli)", 140.00, None, 4),

    ("porsiyon", "p1",   "1 Porsiyon",        "Standart Porsiyon",     0.00, 1.000, 1),
    ("porsiyon", "p1_5", "1.5 Porsiyon",      "%40 Ekstra Porsiyon",   0.00, 1.400, 2),
    ("porsiyon", "p2",   "2 Porsiyon (Çift)", "Doyurucu Çift Porsiyon", 0.00, 1.800, 3),

    ("ekstra", "kaymak",   "Ekstra Manda Kaymağı",         None, 35.00, None, 1),
    ("ekstra", "dondurma", "Ekstra Maraş Dondurması",      None, 40.00, None, 2),
    ("ekstra", "cikolata", "Ekstra Belçika Çikolata Sosu", None, 25.00, None, 3),
    ("ekstra", "fistik",   "Ekstra Antep Fıstığı Tozu",    None, 30.00, None, 4),
]

INSERT_OPSIYON = """
IF NOT EXISTS (SELECT 1 FROM UrunOpsiyonlari WHERE kod = ?)
    INSERT INTO UrunOpsiyonlari (grup, kod, ad, aciklama, fiyat_farki, fiyat_carpani, siralama)
    VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def migrate() -> None:
    conn, _ = get_db_connection(autocommit=False)
    cursor = conn.cursor()
    try:
        cursor.execute(CREATE_URUN_OPSIYONLARI)
        cursor.execute(CREATE_SIPARIS_DETAY_OPSIYONLARI)

        # `IF NOT EXISTS ... INSERT` toplu ifadesinde `cursor.rowcount` INSERT'in
        # gercekten calisip calismadigini guvenilir bicimde bildirmiyor. Satir
        # sayisini once ve sonra saymak, ne oldugunu yanilmadan soyler.
        cursor.execute("SELECT COUNT(*) FROM UrunOpsiyonlari")
        onceki = int(cursor.fetchone()[0])

        for grup, kod, ad, aciklama, fark, carpan, sira in OPSIYONLAR:
            cursor.execute(
                INSERT_OPSIYON,
                (kod, grup, kod, ad, aciklama, fark, carpan, sira),
            )

        cursor.execute("SELECT COUNT(*) FROM UrunOpsiyonlari")
        sonraki = int(cursor.fetchone()[0])

        conn.commit()
        print(f"Opsiyon satiri: onceki {onceki} -> simdi {sonraki} (yeni {sonraki - onceki})")

        cursor.execute(
            "SELECT grup, kod, ad, fiyat_farki, fiyat_carpani FROM UrunOpsiyonlari ORDER BY grup, siralama"
        )
        print("\nTablodaki opsiyonlar:")
        for row in cursor.fetchall():
            print(f"  {row[0]:<9} {row[1]:<9} {row[2]:<30} fark={row[3]} carpan={row[4]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def rollback() -> None:
    """Tablolari geri alir. Onay istenmesinin nedeni: siparis opsiyon kaydi silinir."""
    conn, _ = get_db_connection(autocommit=False)
    cursor = conn.cursor()
    try:
        cursor.execute("IF OBJECT_ID('SiparisDetayOpsiyonlari','U') IS NOT NULL DROP TABLE SiparisDetayOpsiyonlari")
        cursor.execute("IF OBJECT_ID('UrunOpsiyonlari','U') IS NOT NULL DROP TABLE UrunOpsiyonlari")
        conn.commit()
        print("Tablolar silindi. Siparis kayitlarina dokunulmadi.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback()
    else:
        migrate()
        print("\nIslem tamamlandi.")
