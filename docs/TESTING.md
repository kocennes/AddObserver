bak ba# Test stratejisi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17

## Amaç

Değişikliklerin iş kabul kriterlerini, public kullanıcı izolasyonunu, insan onayı kapısını ve dış servis
sözleşmelerini gerçek müşteri verisi kullanmadan kanıtlayan test yaklaşımını belirlemek.

## Araştırma

- Google Ads [test accounts](https://developers.google.com/google-ads/api/docs/best-practices/test-accounts),
  geliştirme sırasında üretim hesaplarını etkilemeden API çağrılarının denenmesini sağlar; ancak test
  hesaplarının reklam sunmadığı ve bazı özellikleri kapsamadığı dikkate alınmalıdır.
- [OWASP Web Security Testing Guide](https://owasp.org/www-project-web-security-testing-guide/), auth,
  authorization, session, input validation ve business logic için sistematik negatif test alanları sunar.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) klavye, focus, reflow, hata önleme ve name/role/value gibi
  UI kabul kriterlerinin normatif kaynağıdır.
- MCP [Tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), input/output
  schema ve tool error davranışının contract testlerinde doğrulanabilecek sınırlarını tanımlar.

## Karar

Testler risk tabanlı piramit ve aşağıdaki merge kapılarıyla uygulanır. Canlı Anthropic/Google testleri
deterministik CI kapısının parçası değildir; resmi client mock'ları ve ayrılmış Google test hesabı kullanılır.
**Sonraki gözden geçirme:** 2026-10-17

## Kalite kapısı

Merge için format/lint, type check, unit, integration, güvenlik negatif testleri ve secret/dependency
taraması başarılıdır. Testler gerçek müşteri credential'ı veya hesabı kullanmaz.

İlk iskelette harici bağımlılık gerektirmeyen `unittest` runner kullanılır. Bağlayıcı belgelerin yaşam
döngüsü metadata'sı, yerel Markdown bağlantıları ve `DOCUMENTATION.md` matrisindeki belge hedefleri
`python tools/check_docs.py` ile doğrulanır. Uygulama bağımlılıkları seçildiğinde pytest, formatter,
linter, type checker ve coverage eşiği aynı belgede karara bağlanıp kilitli geliştirme bağımlılıklarıyla
eklenecektir.

## Test piramidi

- **Unit:** şema, policy, para micros dönüşümü, state machine, retry sınıflandırma.
- **Integration:** DB isolation, repository filtreleri, approval→execution, audit atomicity.
- **Contract:** Google Ads ve MCP adapter request/response eşlemeleri; resmi client mock/fake.
- **UI:** bileşen durumları, klavye, erişilebilir adlar, onay özeti ve error recovery.
- **E2E:** test hesabı veya tamamıyla fake servisle veri→öneri→onay→uygulama→audit.

## Zorunlu güvenlik vakaları

1. Onay olmadan execution çağrısı Google mutate üretmez.
2. Süresi dolmuş, reddedilmiş, hash'i değişmiş veya başka principal'a ait onay reddedilir.
   Approval persistence, proposal ile aynı principal kapsamında değilse repository ve DB
   bütünlük katmanlarında reddedilir.
   Execution reservation da proposal'nın principal, customer ve onaylanmış hash snapshot'ıyla
   eşleşmeden kaydedilemez.
3. `customer_id` değiştirerek cross-user erişim mümkün değildir.
   Çakışan bir `proposal_id`, başka principal veya customer kapsamındaki mevcut öneriyi
   güncellemek ya da taşımak için kullanılamaz.
   Başka principal'a ait bir `execution_id` ile sonuç durumu veya Google request ID güncellenemez.
4. Yanlış principal credential'ı secrets manager'dan çözülemez.
5. Yeni campaign `PAUSED` değilse adapter isteği reddeder.
6. Audit başlangıç kaydı yazılamıyorsa mutate yapılmaz.
   Bu durumda execution `failed` olur; aynı idempotency anahtarıyla tekrar da provider mutate üretmez.
   Execution başlangıç ve sonuç audit olayları aynı `execution_id` ile ilişkilendirilir.
7. Prompt injection içeren reklam metni tool scope/argümanını değiştirmez.
8. Duplicate idempotency key tek execution ve tek provider mutate üretir; aynı anahtar farklı
   principal, proposal veya payload kapsamıyla tekrar kullanılırsa fail-closed reddedilir.
9. Retryable/non-retryable Google Ads hataları doğru ayrılır; belirsiz mutate kör tekrarlanmaz.
   Adapter istisnası execution'ı `unknown` yapar ve `execution.completed` audit sonucu üretir;
   sonuç audit'i de yazılamazsa audit hatası asıl adapter hatasını exception chain içinde korur.
   Adapter non-terminal/geçersiz sonuç döndürürse de execution ve completion audit `unknown` olur.
10. Log capture içinde token, secret ve authorization header bulunmaz.
11. MCP auth yokken `401` + doğru `WWW-Authenticate resource_metadata` döner; metadata resource exact match'tir.
12. PKCE, audience, issuer, redirect URI, refresh replay ve shared-client account-linking negatif testleri geçer.
13. Her tool annotation/title, ≤64 karakter ad ve read/write ayrımı review kriterlerine uyar.

## Mock politikası

- Her yeni Google Ads çağrısında başarılı sonuç, API hatası, timeout/rate limit ve ownership testi bulunur.
- Mock, resmi client'ın çağrı imzasına yakın tutulur; uydurma basitleştirilmiş API davranışı kullanılmaz.
- Test fixture'ları açıkça sahte ID ve metinler taşır; üretim dump'ı kullanılmaz.
- Model cevabı deterministik fixture/schema ile test edilir; canlı Anthropic çağrısı CI kalite kapısı değildir.

## Erişilebilirlik ve performans

- Otomatik axe benzeri tarama manuel klavye/screen reader kontrolünün yerine geçmez.
- Kritik onay akışı 320 CSS px, %200 zoom ve reduced motion ile test edilir.
- Quota ve concurrency davranışı kontrollü yük testinde doğrulanır; üretim Google hesabına yük testi yapılmaz.

## Tamamlanma tanımı

Kod, test, ilgili belge, migration/rollback ve gözlemlenebilirlik birlikte teslim edilmedikçe iş tamamlanmış sayılmaz.

## Açık sorular

- Uygulama iskeleti için pytest, Python formatter/linter/type checker seçimi ve minimum coverage eşiği.
- Google Ads test hesabıyla çalışan opt-in contract testlerinin ortamı ve sıklığı.
- UI teknoloji seçimine göre accessibility/E2E araçları.
- DAST ve mutation testing'in hangi fazda kalite kapısına alınacağı.

## Güncelleme geçmişi

- 2026-07-17 — Execution sonuç güncellemesinin principal filtresi ve cross-principal negatif testi eklendi.
- 2026-07-17 — Başlangıç audit hatasının execution'ı `failed` sonlandırdığı ve idempotent
  tekrarın provider'a gitmediği regresyon kapısına eklendi.
- 2026-07-17 — Execution→proposal ilişkisinde composite foreign key ile principal izolasyonu ve
  customer/hash snapshot doğrulaması eklendi.
- 2026-07-17 — Approval→proposal ilişkisinde uygulama kontrolü ve composite foreign key ile
  cross-principal bütünlük testi eklendi.
- 2026-07-17 — Çakışan proposal kimliğiyle cross-principal/cross-customer repository
  güncellemesini reddeden negatif testler eklendi.
- 2026-07-17 — Execution başlangıç/sonuç audit olaylarının aynı `execution_id` ile
  ilişkilendirilmesi test kapısına eklendi.
- 2026-07-17 — Belirsiz adapter istisnasında `unknown` completion audit kaydı ve audit yazma
  hatasında asıl provider hatasının exception chain ile korunması test kapısına eklendi.
- 2026-07-17 — Idempotency kapısı provider mutate tekilliğini ve principal/payload kapsam
  uyuşmazlığının fail-closed reddini kapsayacak şekilde genişletildi.
- 2026-07-17 — Audit başlangıç kaydı başarısızken provider mutate çağrısının yapılmadığı ve belirsiz
  adapter hatasının kör retry yerine `unknown` execution ürettiği uygulama sınırı testleri eklendi.
- 2026-07-17 — Approval domain için onaysız execution, cross-principal, değişmiş hash, expiry ve başarılı rezervasyon testleri eklendi.
- 2026-07-17 — Bağımlılıksız ilk test runner'ı ve otomatik dokümantasyon kapısı tanımlandı.
- 2026-07-17 — Güvenlik negatif testleri, mock politikası ve WCAG kalite kapıları tanımlandı; zorunlu belge formatı eklendi.
