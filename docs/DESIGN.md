# Arayüz ve tasarım sistemi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

Bu belge UI tasarlamadan veya frontend kodu yazmadan önce okunur. Hedef gösterişli bir dashboard
değil; yoğun reklam verisini hızlı, sakin ve hataya dayanıklı biçimde karar ekranına çevirmektir.

## Amaç

Claude içi tool deneyimi ve varsa onboarding/onay web UI'ının erişilebilir, açık izinli ve hata önleyici
tasarım standardını belirlemek.

## Araştırma

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) kontrast, klavye, focus, reflow ve hata önleme ölçütlerini tanımlar.
- Anthropic [connector review criteria](https://claude.com/docs/connectors/building/review-criteria) dar tool
  açıklaması, actionable hata ve read/write ayrımı ister.

## Karar

## Tasarım ilkeleri

1. **Hesap bağlamı görünür:** Müşteri, Ads customer ID ve tarih aralığı sayfanın kalıcı başlığındadır.
2. **Öneri ile gerçeği ayır:** AI önerisi, kaynak metrik ve Google Ads'te uygulanmış durum farklı görsel
   etiketlerle sunulur.
3. **Değişikliği göster:** Onay, yalnız “Uygula” düğmesi değildir; eski → yeni değer ve etki görünürdür.
4. **Renk tek sinyal değildir:** Durumlar ikon + metin + renk kullanır.
5. **Yoğunluk kontrollüdür:** Özet önce, detay isteğe bağlı; kritik bilgi accordion içine saklanmaz.
6. **Geri bildirim doğrulanmıştır:** İşlem başlatıldı, Google kabul etti ve kısmi başarısızlık ayrı durumlardır.

## Bilgi mimarisi

- **Genel bakış:** hesap sağlığı, veri tazeliği, bekleyen onay, son uygulamalar.
- **Öneriler:** filtrelenebilir liste; risk, tür, oluşturulma zamanı ve durum.
- **Öneri detayı:** kaynak metrikler, model gerekçesi, eski/yeni değer, riskler, onay geçmişi.
- **Uygulama geçmişi:** sonuç, hata, Google request ID ve audit referansı.
- **Ayarlar:** hesap bağlantıları ve roller; secret değerleri hiçbir zaman görüntülenmez.

## Onay etkileşimi

- Birincil eylem “Değişikliği incele”; detay görülmeden toplu onay yoktur.
- Onay modalı seçili hesap, değişiklik sayısı, eski/yeni değer ve geri alma bilgisini tekrarlar.
- Yıkıcı/yüksek etkili işlem açık metinli onay ve gerekiyorsa ikinci onay gerektirir.
- Buton metinleri sonuç odaklıdır: “Bütçeyi ₺5.000 olarak onayla”; belirsiz “Tamam” kullanılmaz.
- Çift tıklama/yenileme duplicate mutate üretmez. Bekleme durumunda buton kilitlenir fakat iptal/gezinme
  davranışı açıklanır.
- Hata, alanın yanında ve sayfa özetinde görünür; kullanıcı girdisi korunur.

## Görsel temel

- 4 px tabanlı spacing ölçeği: `4, 8, 12, 16, 24, 32, 48`.
- En fazla üç tipografik seviye aynı görünümde baskın olur; sayılar tabular numerals kullanır.
- Para birimi, yüzde, timezone ve tarih aralığı açık yazılır; yalnız renge veya yuvarlanmış sayıya güvenilmez.
- Desktop-first operasyon ekranı responsive olur: 1280+ iki kolon, 768–1279 tek kolon/çekmece,
  daha dar görünümde tablo kartlara dönüşür. Kritik onay mobilde de tamamlanabilir.
- Tokenlar (`color`, `space`, `type`, `radius`, `shadow`, `z-index`) kod içinde tek kaynaktan gelir.

## Erişilebilirlik — WCAG 2.2 AA

[WCAG 2.2](https://www.w3.org/TR/WCAG22/) asgari standarttır.

- Normal metin kontrastı en az 4.5:1, büyük metin 3:1; UI sınırları ve durum göstergeleri 3:1.
- Tüm işlevler klavyeyle çalışır; mantıklı focus sırası ve görünür focus vardır. Hedef, W3C'nin
  [focus görünümü](https://www.w3.org/WAI/WCAG22/Understanding/focus-appearance) rehberindeki
  2 px çevre ve 3:1 değişim seviyesidir.
- Pointer hedefleri en az 24×24 CSS px veya yeterli aralığa sahiptir
  ([Target Size Minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum)).
- Modal focus'u içine alır, başlığa taşır ve kapanınca tetikleyene döndürür; Escape davranışı güvenli ise çalışır.
- Form alanlarında görünür label, programatik ad, yardım ve hata bağlantısı bulunur.
- Dinamik işlem durumları uygun `aria-live` bölgesinde duyurulur; tablo başlıkları ve caption semantiktir.
- %200 zoom ve 320 CSS px reflow test edilir. `prefers-reduced-motion` desteklenir.

## Bileşen kabul kriterleri

- Her bileşenin boş, yükleniyor, başarı, hata, disabled ve yetkisiz durumu tasarlanır.
- Story/example düzeyinde klavye ve screen reader adı doğrulanır.
- Para/bütçe alanları locale gösterimi ile API'nin micros değeri arasında açık dönüşüm testi taşır.
- Screenshot tek başına tasarım kaynağı değildir; token ve davranışlar yazılıdır.

## Tasarım teslim kontrolü

- Kullanıcı, hesap ve değişikliğin etkisini onaydan önce anlayabiliyor mu?
- Loading/error/empty/partial-success ve stale-data durumları var mı?
- Klavye, zoom, kontrast ve screen reader kontrol edildi mi?
- PII/secret ekrana veya telemetry'ye sızıyor mu?
- Kabul kriteri `PRODUCT.md` ile, güvenlik akışı `SECURITY.md` ile tutarlı mı?

## Açık sorular

- MCP Apps kullanılırsa Anthropic cross-platform tasarım gereksinimleri (bugün MCP Apps
  kullanılmadığı için uygulanmaz durumda, bkz. Güncelleme geçmişi).

## Güncelleme geçmişi

- 2026-07-18 — Faz 1.3: "İlk fazda ayrı, tasarım sistemi uygulanmış bir web dashboard olup
  olmayacağı" sorusu kapatıldı: **Faz 1'de yaratılmaz.** Deneyimin tamamı Claude içi tool
  akışında ve mevcut minimal/stilsiz semantik `/approvals` onay sayfasında kalır; yeni bir
  frontend framework veya marka tasarım sistemi eklenmez. Gerekçe: (1) `/approvals` zaten
  erişilebilirlik/onay ilkelerini karşılıyor (bkz. aşağıdaki 2026-07-17 notu), ayrı bir
  dashboard bugün hiçbir kabul kriterini karşılamak için ZORUNLU değil; (2) Faz 1 kapsamı
  reporting + local proposal'dır (`docs/PRODUCT.md`), account-management/onboarding
  dashboard'unun asıl gerekçesi olan gerçek Google Ads write henüz açık değil
  (`docs/GOOGLE_API_ACCESS.md`, RMF sınıflandırması bekleniyor); (3) yeni bir frontend
  yüzeyi ek bakım yükü, erişilebilirlik test yüzeyi ve Anthropic submission incelemesi
  gerektirir, kapsam netleşmeden bu maliyeti üstlenmek erken. MCP Apps UI de aynı gerekçeyle
  Faz 1'e eklenmez. Yeniden değerlendirme tetikleyicisi: `todo.md` 1.1/8.x write kapsamı
  açılırsa veya kullanıcı geri bildirimi `/approvals`'ın yetersiz kaldığını gösterirse.
  Kod değişikliği yoktur. `docs/ARCHITECTURE.md` ve `docs/PRODUCT.md` "Açık sorular"
  bölümleri de bu kararla güncellendi.
- 2026-07-17 — `/approvals`'ın minimal, semantik HTML ile Onay etkileşimi/erişilebilirlik
  ilkelerini karşıladığı, ayrı tasarım sistemli dashboard sorusunun ise açık kaldığı not edildi.
- 2026-07-17 — Public connector consent ve Claude içi deneyim bağlamı eklendi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
