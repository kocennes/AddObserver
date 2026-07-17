Bu dosyadaki metni, VS Code içinde Claude Code veya Codex'e ilk mesaj olarak yapıştırın.

---

Bu proje, Anthropic'in Claude Connectors Directory'sinde yayınlanacak, **herkese açık ve
tamamen ücretsiz bir Google Ads connector'ü**. Her kullanıcı kendi Google Ads hesabını kendi
OAuth izniyle bağlar; Claude performansı analiz eder, öneriler kullanıcı onayından geçtikten
sonra Google Ads'e yazılır. Supermetrics'in yaptığına benzer, ama sadece Google Ads'e özel ve
ücretsiz — hiçbir ödeme/abonelik altyapısı kurulmayacak.

Başlamadan önce sırasıyla:

1. `AGENTS.md` ve `CLAUDE.md` dosyalarını oku.
2. `docs/ARCHITECTURE.md` üzerinden genel akışı incele.
3. `docs/DOCUMENTATION.md` içindeki iş→belge matrisini oku. İlgili belgeler eksik veya güncel
   değilse **kod yazmadan önce** resmî güncel kaynaklarla tamamla. Sırayla:
   - `docs/SECURITY.md`:
     - OAuth token güvenliği ve saklanması
     - Google Ads API'nin resmi güvenlik/kullanım politikaları
     - Çok kullanıcılı (multi-tenant) veri izolasyonu
     - MCP sunucu güvenliği
     - Secrets/API key yönetimi
     - Google'ın hassas kapsamlar (sensitive/restricted scopes) için OAuth app verification
       süreci — herkese açık, çok kullanıcılı bir uygulama olduğumuz için gerekebilir
   - `docs/GOOGLE_API_ACCESS.md` — Basic vs Standard Access, Required Minimum Functionality
     (RMF) kuralları, bu connector için hangi seviyenin gerekli olduğu
   - `docs/CONNECTOR_SUBMISSION.md` — Anthropic Connectors Directory'nin güncel teknik ve
     politika gereksinimleri (OAuth 2.1 + PKCE, Streamable HTTP transport, tool annotation,
     gizlilik politikası, destek kanalı, reviewer test hesabı)
   konularında web'den güncel araştırma yap, bulgularını kaynaklarıyla birlikte yaz.
4. Güvenlik için `SECURITY.md`, erişim seviyesi için `GOOGLE_API_ACCESS.md`, directory başvurusu
   için `CONNECTOR_SUBMISSION.md`, UI için `PRODUCT.md` + `DESIGN.md`, veri için `DATA_MODEL.md`,
   Google Ads için `API_CONTRACTS.md`, MCP için `MCP.md`, test için `TESTING.md`, operasyon için
   `OPERATIONS.md` ve hukuki metinler için `LEGAL.md` bağlayıcıdır. Büyük kararları
   `docs/decisions/` altında ADR olarak kaydet.
5. İlgili belgeler tamamlandığında bana kısa bir özet sun (özellikle Basic mi Standard Access mi
   gerektiği ve Anthropic başvurusu için eksik kalan noktalar), onayımı bekle.
6. Onay sonrası `backend/src/auth/` ile başla (OAuth 2.1 + PKCE akışı) — küçük, test
   edilebilir parçalar halinde ilerle, her adımda ne yaptığını özetle.
7. Önemli bir mimari kararda bana danış; varsayım yapman gerekiyorsa açıkça belirt ve devam et.

Not: `docs/SECURITY.md` tek örnek değil — `AGENTS.md` → "Yeni bir tasarım alanı ortaya çıkarsa"
bölümünde tanımlandığı gibi, veri modeli, API/tool tasarımı, hata yönetimi, rate limit,
loglama, dağıtım, hukuki (`docs/LEGAL.md`) ve test stratejisi gibi her tasarım alanı için de
ilgili koda başlamadan önce karşılık gelen `docs/<ALAN>.md` dosyasını aynı desenle oluştur.

Teknoloji: Python 3.11+, resmi `google-ads` kütüphanesi, resmi Anthropic MCP Python SDK ile
uzak (remote) MCP sunucusu, Streamable HTTP transport, OAuth 2.1 + PKCE. Farklı bir yığın
öneriyorsan önce gerekçeni söyle, onaysız değiştirme.

Şu anki hedef: **tek bir test kullanıcısı/hesabı için uçtan uca çalışan bir prototip**
(OAuth ile bağlan → veri çek → Claude analiz → onay ekranı → yazma). Directory'ye submission
ve çoklu kullanıcı ölçeklendirmesi sonraki aşama.
