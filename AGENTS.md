# Proje ve Arayüz Kuralları (AGENTS.md)

Bu dosya, yapay zekanın bu projede ve gelecek geliştirmelerde uyması gereken zorunlu tasarım ve performans kurallarını içerir.

## Mobil ve Arayüz Performans Kuralları (QR Menü & Garson Paneli)

### 1. Yasaklı Ağır CSS Efektleri (Kesinlikle Kullanılmayacak)
* **`backdrop-filter: blur(...)` YASAĞI:** Mobil GPU ve Web View performansını aşırı kastırdığı için hiçbir navbar, kart, modal veya panelde `backdrop-filter` / `blur` kullanılmayacaktır. Bunun yerine yüksek opaklıklı düz/sade renkler (`rgba(11, 15, 25, 0.95)` vb.) kullanılacaktır.
* **`background-attachment: fixed` & Canlı Radyal Gradyan YASAĞI:** Kaydırma (scroll) anında sayfanın sürekli yeniden çizilmesine (repaint) yol açtığı için mobil arka planlarda `fixed` attachment ve karmaşık radyal gradyanlar kullanılmayacaktır.
* **`transition: all` YASAĞI:** Sayfa elemanlarında genel `transition: all` kesinlikle kullanılmayacak; sadece gerekli elemanlara nokta atışı animasyon (`transition: transform 0.15s ease, opacity 0.15s ease`) verilecektir.
* **Çok Katmanlı Ağır Gölgeler (`box-shadow` / Glow):** Mobil ekran kartını yoran ağır glow/parlama ve multi-layer gölgeler yerine sade sınır çizgileri (`border`) veya hafif standart gölgeler tercih edilecektir.

### 2. Performans Odaklı Tasarım Prensibi
* Görsel süslemelerden çok **60 FPS mobil performans ve hız** önceliklidir.
* Arayüzler yavaş/kasan ağır tasarımlar yerine son derece hafif, akıcı, hızlı tepki veren (lightweight) yapıda olacaktır.
* Resimler ve görseller WebP formatında ve mobil ekranlara uygun boyutlandırılmış olarak yüklenecektir.
* DOM güncellemelerinde tüm listeyi sıfırdan re-render etmek (`innerHTML` sıfırlaması) yerine sadece değişen elemanlar güncellenecektir.

### 3. Veritabanı ve SQL Kuralları (Zorunlu)
* **Kod İçine SQL Tablo/Migrasyon Yazma YASAĞI:** Projenin Python / Backend kodları içerisinde doğrudan SQL tablo oluşturma, şema değiştirme veya DDL/DML migrasyon sorguları yazılmayacaktır.
* **Kullanıcı Onayı ve SQL İletimi:** Veritabanında yapılması gereken sorgular açıklanıp SQL metni olarak kullanıcıya verilecek, onay alınmadan ve kullanıcı çalıştırmadan veritabanı kodu projeye gömülmeyecektir.

### 4. Geliştirme, Dürüstlük ve Test Zorunluluğu
* **Test Edilmemiş Özellik Eklenmemiş Özelliktir:** Yapılan her yeni düzeltme (fix) veya özellik geliştirmesinden sonra mutlaka gerçek kod akışı ve senaryolar üzerinde dürüstçe test edilecektir.
* **Dürüst Test Yorumu:** Yapılan testlerin neticeleri uydurma veya varsayımsal yorumlar yapılmadan, doğrudan elde edilen somut bulgulara dayanarak dürüstçe raporlanacaktır.
* **Bilmediğini Kabullenme Prensipleri:** Emin olunmayan veya bilinmeyen bir durum olduğunda asla uydurma bilgi veya farazi varsayımlar yapılmayacak, kaynak kod ve loglar üzerinden doğrulanacaktır.

### 5. Yazılım Mimarisi ve DTO / Response Kuralları (Zorunlu)
* **Ayrı Response Objesi / DTO Kullanımı:** API yanıtlarında (Response) doğrudan veritabanı entity'leri veya iç Pydantic modelleri döndürülmeyecektir. Gereğinden fazla veya hassas bilgi gönderimini engellemek amacıyla sadece gerekli alanları içeren ayrı Response DTO / Pydantic modelleri kullanılacaktır.
* **Katmanlı Mimari ve Dependency Injection:** Kod yapısı Controller (Router/API), Service (İş Mantığı) ve DB (Repository/Veri Erişim) katmanlarına kesin olarak ayrılacak; katmanlar arasındaki bağımlılıklar Dependency Injection (Bağımlılık Enjeksiyonu) üzerinden yönetilecektir.
