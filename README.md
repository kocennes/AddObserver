# Ad Ops Agent — başlangıç iskeleti

Herkese açık, Anthropic'in Claude Connectors Directory'sinde yayınlanacak, **tamamen ücretsiz**
bir Google Ads connector'ü. Her kullanıcı kendi Google Ads hesabını kendi OAuth izniyle bağlar;
Claude performansı analiz eder, öneriler kullanıcı onayından geçtikten sonra Google Ads'e
yazılır. Ödeme/abonelik altyapısı yoktur ve eklenmeyecektir.

## İlk adımlar
1. `AGENTS.md` dosyasını okuyun — tüm proje kuralları burada.
2. `CLAUDE.md` dosyasına bakın — Claude Code'a özel notlar.
3. `STARTER_PROMPT.md` içindeki metni VS Code'da Claude Code / Codex'e ilk mesaj olarak verin.
4. `docs/DOCUMENTATION.md` içindeki iş→belge matrisini izleyin.
5. Kod yazmadan önce sırasıyla `docs/SECURITY.md`, `docs/GOOGLE_API_ACCESS.md` (Basic/Standard
   Access, RMF uyumu) ve `docs/CONNECTOR_SUBMISSION.md` (Anthropic Connectors Directory
   başvuru gereksinimleri) okunmalı; `Taslak` veya bloklayıcı açık karara bağlı alanda kod yazılmamalı.
6. Güvenlik, tasarım, veri, API, MCP, test, operasyon ve hukuki belgeler kod için bağlayıcıdır;
   davranış değiştiğinde ilgili belge de aynı değişiklikte güncellenir.

## Mevcut doğrulama komutları

```powershell
python tools/check_docs.py
python -m unittest discover -s backend/tests -v
```

Mimari, ürün, auth, veri modeli, API/MCP sözleşmesi ve tasarım kararları ürün sahibi tarafından
onaylanıp `Kabul edildi` durumuna geçirildi; `backend/` iskeleti bu kararlar üzerine küçük, test
edilebilir adımlarla inşa ediliyor (bkz. `docs/decisions/0001-backend-stack.md`). `LEGAL.md` ve
`GOOGLE_API_ACCESS.md` hukukçu incelemesi ve Google Compliance/RMF sınıflandırması gelene kadar
`Taslak` kalır; bunlara bağımlı alanlarda (ödeme dışı gerçek public lansman, management/write
tool'ları) implementasyon yapılmaz.

## Klasör yapısı
```
AGENTS.md              — tüm ajanlar için ana talimat dosyası
CLAUDE.md               — Claude Code'a özel kısa notlar
STARTER_PROMPT.md        — ajana verilecek ilk mesaj
PRIVACY_POLICY.md, TERMS.md — hukukçu/işletmeci bilgisi bekleyen public politika taslakları
docs/
  DOCUMENTATION.md         — işe göre zorunlu okuma ve güncelleme matrisi
  ARCHITECTURE.md          — sistem sınırları ve uçtan uca akış
  SECURITY.md              — güvenlik standardı ve kaynaklı kontrol kapıları
  GOOGLE_API_ACCESS.md      — Google Ads erişim seviyesi, RMF ve OAuth verification
  CONNECTOR_SUBMISSION.md    — Anthropic Connectors Directory başvuru hazırlığı
  LEGAL.md                    — gizlilik, veri yaşam döngüsü ve kullanım şartları kararları
  PRODUCT.md               — kapsam, roller, akışlar ve kabul kriterleri
  DESIGN.md                — UI/UX sistemi ve WCAG 2.2 AA
  DATA_MODEL.md            — varlıklar, izolasyon ve retention
  DATABASE.md              — PostgreSQL, RLS, transaction ve migration kararları
  AUTH.md                  — uygulama oturumu ve Google OAuth yaşam döngüsü
  API_DESIGN.md            — HTTP/MCP yüzeyinin tasarım standardı
  API_CONTRACTS.md         — HTTP ve Google Ads sözleşmeleri
  MCP.md                   — MCP tool ve model güvenliği
  ERROR_HANDLING.md        — hata taksonomisi, retry ve belirsiz mutate
  RATE_LIMITS.md           — kota bütçesi, fair queue ve throttling
  OBSERVABILITY.md         — log, metric, trace ve append-only audit
  TESTING.md               — test stratejisi ve kalite kapıları
  DEPLOYMENT.md            — CI/CD, immutable image ve ortam izolasyonu
  OPERATIONS.md            — deploy, izleme ve olay runbook'ları
  REPOSITORY.md            — GitHub remote ve Git çalışma düzeni
  decisions/               — mimari karar kayıtları (ADR)
backend/
  src/
    api/                    — Google Ads API istemcisi (customer_id parametreli, çoklu kullanıcı)
    mcp/                     — Claude'a bağlanan uzak (remote) MCP sunucusu, Streamable HTTP
    auth/                     — Her kullanıcı için OAuth 2.1 + PKCE akışı, token yönetimi
    approval/                  — insan onay iş akışı
    db/                         — kullanıcı/hesap eşlemesi, denetim kayıtları
  tests/
.env.example
.gitignore
```

## Belge önceliği

Bir işe başlamadan önce `docs/DOCUMENTATION.md` okunur. Örneğin bir onay ekranı tasarlanacaksa
`PRODUCT.md`, `DESIGN.md` ve `SECURITY.md`; yeni MCP tool'u yazılacaksa `MCP.md`,
`API_CONTRACTS.md`, `SECURITY.md` ve `CONNECTOR_SUBMISSION.md`; Google Ads erişimiyle ilgili
bir karar için `GOOGLE_API_ACCESS.md` uygulanır. Belge ile kod çelişirse önce karar netleştirilir
ve belge güncellenir.

> **Not:** Proje dışa kapalı ajans aracından public connector'a pivot etti. Temel mimari, ürün, auth,
> güvenlik ve kullanıcı izolasyonu belgeleri yeni modele çevrildi ve 2026-07-17'de ürün sahibi
> tarafından `Kabul edildi`. Her belgenin `Durum` alanı uygulanabilirlik kapısı olmaya devam eder.
> Hukuki metinler (`LEGAL.md`) ve Google Compliance/RMF sınıflandırması (`GOOGLE_API_ACCESS.md`)
> halen bloklayıcı, dışa bağımlı açık kararlardır.
