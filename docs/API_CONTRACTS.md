# API ve Google Ads sözleşmeleri

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18

## Amaç

Public MCP'nin kullandığı iç HTTP kaynakları, Google Ads adapter sınırı ve proposal payload'ının test
edilebilir veri sözleşmelerini tanımlamak.

## Araştırma

- [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) güvenli makine-okur HTTP problem modelini tanımlar.
- Google Ads [API structure](https://developers.google.com/google-ads/api/docs/concepts/api-structure)
  `validate_only`; [mutate best practices](https://developers.google.com/google-ads/api/docs/mutating/best-practices)
  operasyon/response davranışını açıklar.
- Google Ads [List Accessible Accounts](https://developers.google.com/google-ads/api/docs/account-management/listing-accounts)
  `ListAccessibleCustomers` çağrısının yalnız OAuth kullanıcısının doğrudan eriştiği hesapları döndürdüğünü,
  customer ID gerektirmediğini ve verilmiş `login-customer-id` değerini yok saydığını; [account hierarchy](https://developers.google.com/google-ads/api/docs/account-management/get-account-hierarchy)
  rehberi manager alt hesaplarının ayrı `customer_client` sorgusuyla keşfedildiğini açıklar.
- Google Ads [v24 ad group fields](https://developers.google.com/google-ads/api/fields/v24/ad_group)
  ad group alanlarıyla birlikte seçilebilen metrics/segments matrisini; [v19+ paging](https://developers.google.com/google-ads/api/docs/reporting/paging)
  `Search` sayfa boyutunun sabit 10.000 olduğunu, `page_size` alanının kaldırıldığını ve sonraki isteğin aynı
  sorgu + `next_page_token` ile yapılması gerektiğini tanımlar.
- Anthropic [review criteria](https://claude.com/docs/connectors/building/review-criteria) catch-all read/write
  tool'ları reddeder ve amaç-özel tool bekler.

## Karar
**Sonraki gözden geçirme:** 2026-10-17

## Genel sözleşme

- Dış API sürümlüdür (`/api/v1`). Request/response katı şemalıdır; bilinmeyen mutate alanları reddedilir.
- Connector token subject'inden türetilen principal bağlamı bütün servis/repository çağrılarına açıkça aktarılır.
- Canlı read/proposal kaynakları yalnız `active` ads_account eşleşmelerini kullanır; `disconnected` satırlar
  geçmiş/audit için tutulur ama gelecekteki erişim kanıtı sayılmaz.
- State-changing istekler `Idempotency-Key`, correlation ID ve yetkili kullanıcı gerektirir.
- Zaman RFC 3339 UTC, para integer micros, ID'ler string olarak taşınır.
- Hatalar kararlı `code`, güvenli `message`, `correlation_id` ve alan detayları döndürür; stack trace dönmez.
  Her public HTTP response `X-Correlation-ID` taşır; güvenli client değeri korunur, geçersiz değer sanitize
  edilip yeni opaque ID ile değiştirilir.
- Public ingress request body sınırı 1 MiB'dir. `Content-Length` bu sınırı aşarsa handler çalışmadan
  `413 application/problem+json` ve `code=request_body_too_large` döner. Başlık olmadan gelen streamed body
  downstream okuma sırasında sınırı geçerse aynı `413` döner; geçersiz değer `400 invalid_content_length` olur.

## Önerilen HTTP kaynakları

| Metot/yol | Amaç | Yetki/koruma |
|---|---|---|
| `GET /api/v1/accounts` | Yetkili Ads hesapları | Principal-scoped, yalnız active hesaplar, uygulandı |
| `GET /api/v1/proposals` | Önerileri listele | Principal + customer scope, opak cursor pagination, uygulandı |
| `GET /api/v1/proposals/{id}` | Değişiklik önizle | Ownership check, uygulandı |
| `POST /api/v1/proposals/{id}/executions` | Onaylı değişikliği uygula | Faz 8'e kadar yayımlanmaz; revalidation, idempotency, audit |

Execution endpoint'i Directory v1/Faz 1'de yayımlanmaz. Faz 8 kapısı açıldığında da ham Google Ads mutate
payload kabul etmez; yalnız önceden doğrulanmış proposal ID uygular.

## Google Ads istemci sınırı

- İç servis çağrısı doğrulanmış `principal_id` ve `customer_id` alır; MCP tool principal ID kabul etmez,
  resource server token subject'inden türetir ve active hesap eşlemesini doğrular.
- GAQL sorguları kodda tanımlı query object/allowlist ile kurulur. Tarih ve ID parametreleri doğrulanır.
- Mutate adapter Directory v1/Faz 1'de yoktur; Faz 8 kapısı açılırsa yalnız `PRODUCT.md` içinde kabul edilmiş
  işlem türlerini destekler.
- `validate_only` mümkün olan işlemlerde onay öncesi kullanılır; başarı canlı uygulama sayılmaz.
- Retry yalnız belgelenmiş retryable hatalarda, jitter ve quota `retry_delay` ile yapılır. Mutate sonucu belirsizse
  önce Google durumu okunur; kör tekrar yapılmaz.
- Google `request_id`, hata kodu ve field path yakalanır; güvenli uygulama hatasına çevrilir.
- Güncel limitler için Google'ın [quota](https://developers.google.com/google-ads/api/docs/best-practices/quotas),
  [error handling](https://developers.google.com/google-ads/api/docs/get-started/handle-errors) ve
  [rate limit](https://developers.google.com/google-ads/api/docs/productionize/rate-limits) belgeleri izlenir.

## Öneri şeması — asgari alanlar

```json
{
  "schema_version": "1",
  "type": "campaign_budget_update",
  "customer_id": "1234567890",
  "resource_name": "customers/1234567890/campaignBudgets/42",
  "before": {"amount_micros": 4000000000},
  "after": {"amount_micros": 5000000000},
  "currency_code": "TRY",
  "reason": "Kaynak metriklere dayalı kısa gerekçe",
  "evidence_refs": ["metric-snapshot-id"],
  "risk": "medium"
}
```

Modelin gönderdiği `customer_id`, `resource_name`, `before` ve bütçe değeri backend tarafından yeniden doğrulanır.
`rationale` en fazla 2000 karakter olabilir ve kontrol karakteri içeremez; durum değişikliği önerilerinde
`current_status` yalnız Google Ads'in gerçek `CampaignStatus` değerlerinden (`ENABLED`/`PAUSED`/`REMOVED`)
biri olabilir; `campaign_id` en fazla 19 haneli (`int64`) sayısal bir kimlik olmalıdır.

## Uyum ve sürümleme

- Google Ads API sürümü ve resmi Python client sürümü lockfile'da sabitlenir.
- Destek sonu tarihleri üç aylık operasyonal kontrolde izlenir.
- Breaking sözleşme yeni API/schema version gerektirir; eski proposal yeni şemaya sessizce çevrilmez.

## Açık sorular

- İlk live management/execution allowlist'i Google RMF sınıflandırmasına bağlıdır.
- Faz 8 execution yüzeyi açılırsa ayrı JSON execution endpoint'ine ihtiyaç olup olmadığı.

## Güncelleme geçmişi

- 2026-07-22 — Faz 6 ürün yüzeyi kararı: public v1 JSON sözleşmesi accounts ve proposal read-only
  endpoint'leriyle sınırlandı. Analiz backend/model endpoint'i, bearer approval decision, public audit ve
  internal admin endpoint'leri yayımlanmaz; onay yalnız browser session+CSRF akışında kalır.

- 2026-07-22 — Faz 5.5 MCP reporting response'ları en fazla 500 satır ve 512 KiB UTF-8 JSON row bütçesiyle
  sınırlandı. Response `row_count` ve `truncated` metadata'sı taşır; devam anahtarı provider token'ını
  açığa çıkarmaz ve principal/customer/report/date/15-dakika expiry bağlamına imzalıdır. Sayfa içi kesme
  aynı provider sayfasını signed row offset ile sürdürür; context değişimi ve bozuk/oversized token Google'a
  ulaşmadan tek güvenli `invalid_page_token` hatasıyla reddedilir.
- 2026-07-22 — Faz 5.4 keyword reporting contract'ı tamamlandı. Sabit `keyword_view` alan allowlist'i;
  criterion ID, keyword text, match type/status ve metriklerin v24 proto eşlemesi; empty/two-page akışı;
  quota/timeout/auth hata politikası ve bütün reporting tool'larını kapsayan principal ownership reddi
  test edildi. Injection-benzeri keyword metninin GAQL'e veya kontrol akışına girmeyip değiştirilmeden yalnız
  `keyword_text` veri alanında döndüğü contract testine alındı; provider metni talimat olarak yorumlanmaz.
- 2026-07-22 — Faz 5.3 ad group reporting contract'ı tamamlandı. Sabit dokuz alanlı `ad_group` GAQL,
  v24 proto mapping, empty/two-page akışı, quota/timeout/auth ortak hata politikası ve bütün reporting
  tool'larında cross-principal ownership reddi test edildi. Güncel resmî paging araştırması sırasında
  v19+ `Search` için kaldırılmış olan `page_size` parametresinin adapter tarafından hâlâ gönderildiği bulundu;
  parametre ve artık geçersiz yerel page-size doğrulaması kaldırıldı, gerçek service wrapper'ın v24 isteğinde
  yalnız `customer_id`, sabit `query` ve `page_token` gönderdiği contract testine alındı.
- 2026-07-22 — Faz 5.2 campaign reporting contract'ı tamamlandı: campaign GAQL'i sekiz alanlı sabit
  allowlist ve doğrulanmış en fazla 90 günlük tarih penceresiyle kilitlendi; tek RPC yalnız istenen sayfayı
  döndürür ve sonraki sayfa çağıran tarafından opaque page token ile ayrıca istenir. Gerçek Google Ads v24
  proto mock'ları başarı, empty page, iki sayfa, integer micros/enum eşleme, quota, timeout, auth ve güvenli
  hata davranışını doğrular. MCP entegrasyon testleri aktif account/credential ownership'ini ve
  cross-principal reddini ayrıca kanıtlar.
- 2026-07-22 — Faz 5.1 tamamlandı. Doğrudan erişilebilir hesap keşfi `api/accounts.py` içinde ayrı,
  read-only bir `ListAccessibleCustomers` adapter'ına bağlandı; adapter resmi client'ı istek başına
  credential ile kurar, bu RPC için `login_customer_id` göndermez, merkezi retry/hata sınıflandırmasını
  kullanır ve provider resource name'lerini yalnız doğrulanmış, benzersiz 10 haneli customer ID'lere
  indirger. `GoogleAdsAccountDiscoveryClient.discover_accounts`, her doğrudan erişilen hesabı kendi
  `login_customer_id`'siyle izole bir client üzerinden sabit `customer_client` GAQL'iyle sorgulayıp
  etkin alt hesapları keşfeder (doğrudan erişim her zaman manager-türevli yoldan önceliklidir; birden
  fazla manager aynı alt hesabı görürse sayısal en küçük doğrudan manager kazanır). Yeni MCP tool'u
  `sync_accessible_accounts` (`LOCAL_SYNC` annotation) keşfedilen kümeyi `AdsAccountRepository`/
  `PostgresAdsAccountRepository.synchronize_accounts` ile principal-scoped, atomik biçimde yerel
  `ads_account` tablosuna yansıtır: artık erişilemeyen hesaplar yalnızca çağıranın principal'ı altında
  `disconnected` yapılır (asla silinmez, asla başka principal'ın satırına dokunmaz), keşfedilen hesaplar
  eklenir veya yeniden `active` yapılır. AUTH-class keşif hatası ortak `deactivate_credential_on_auth_failure`
  yoluna bağlıdır.
- 2026-07-19 — Bearer HTTP API route'larının production veri erişimi PostgreSQL request unit-of-work
  sınırına bağlandı; exact token bootstrap ve principal-scoped sorgu aynı transaction içinde yürür.
- 2026-07-18 — Faz 1.1 kapsam kararı kapatıldı: Directory v1/Faz 1 public sözleşmesi reporting + proposal
  preview/decision yüzeyleriyle sınırlıdır; `executions` endpoint'i ve Google Ads mutate adapter'ı Faz 8'e
  kadar yayımlanmaz.
- 2026-07-18 — Faz 1.5: `GET /api/v1/proposals` opak, imzalı keyset cursor pagination'ı uyguladı
  (bkz. `docs/API_DESIGN.md` "Pagination sözleşmesi"); daha önce yalnız `limit` ile sınırlı, ilk
  sayfanın ötesine geçemeyen ad hoc davranışın yerini aldı. `next_cursor` yalnız daha fazla satır
  varsa döner; farklı principal/customer/status için üretilmiş veya süresi dolmuş bir cursor aynı
  genel `invalid_cursor` hatasıyla reddedilir.
- 2026-07-18 — Öneri şemasında `rationale`, `current_status` ve `campaign_id` için sınır değer/allowlist
  doğrulaması eklendi (bkz. yukarı, "Öneri şeması — asgari alanlar").
- 2026-07-18 — `proposal_id` girdileri bütün public yüzeylerde 1–128 karakterlik URL-safe opaque
  kimlik olarak sınırlandırıldı; geçersiz HTTP girdisi `invalid_proposal_id` problem cevabı üretir.

- 2026-07-18 — HTTP/MCP read ve proposal yolları `disconnected` hesap satırlarını erişimden dışlar;
  tekrar bağlantı aynı customer satırını `active` olarak canlandırır.
- 2026-07-17 — `GET /api/v1/accounts` principal-scoped HTTP endpoint'i uygulandı; connector bearer token
  audience/expiry/revocation doğrulamasını MCP ile ortak kullanır.
- 2026-07-17 — `GET /api/v1/proposals` ve `GET /api/v1/proposals/{id}` read-only endpoint'leri
  principal/customer izolasyonu ve problem+json hata şekliyle uygulandı.
- 2026-07-17 — 1 MiB public ingress request body sınırı, streamed-body uygulaması ve hata kodları eklendi.
- 2026-07-17 — `X-Correlation-ID` response header'ı ve problem response `correlation_id` sözleşmesi eklendi.
- 2026-07-17 — Principal-scoped public connector ve Google Ads sözleşmeleri tanımlandı.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
