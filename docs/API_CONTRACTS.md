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
- Google Ads [`ListAccessibleCustomers`](https://developers.google.com/google-ads/api/reference/rpc/v24/CustomerService/ListAccessibleCustomers)
  yalnız kimliği doğrulanan kullanıcının doğrudan erişebildiği customer resource name'lerini
  döndürür. Manager alt hesapları ayrı
  [`customer_client`](https://developers.google.com/google-ads/api/fields/v24/customer_client)
  hiyerarşi sorgusuyla keşfedilir; bu kaynak manager'ın kendisiyle tüm doğrudan/dolaylı
  client'larını içerir.
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
| `POST /api/v1/analyses` | Analiz başlat | Rate limit, idempotency |
| `GET /api/v1/proposals` | Önerileri listele | Principal + customer scope, opak cursor pagination, uygulandı |
| `GET /api/v1/proposals/{id}` | Değişiklik önizle | Ownership check, uygulandı |
| `POST /api/v1/proposals/{id}/decisions` | Onay/red | CSRF, role, immutable hash |
| `POST /api/v1/proposals/{id}/executions` | Onaylı değişikliği uygula | Faz 8'e kadar yayımlanmaz; revalidation, idempotency, audit |
| `GET /api/v1/audit-events` | Denetim izi | Auditor role, export audit |

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
- Account discovery önce `CustomerService.ListAccessibleCustomers` ile doğrudan kökleri alır; her
  kök için `customer_client` sorgusu manager hiyerarşisini genişletir. Provider'dan gelen resource
  name/ID yalnız tam `customers/{10-digit-id}`/10 haneli biçimde kabul edilir. Alt hesap satırında
  ilgili kök manager `login_customer_id` olarak saklanır; doğrudan manager olmayan hesapta değer
  `null` kalır. Yinelenen alt hesap ilk doğrulanmış kök kapsamıyla deterministik tekilleştirilir.
- Discovery credential'ı yalnız doğrulanmış `principal_id` için çözülür ve adapter sonucuna
  token/secret girmez. Senkronizasyon yalnız aynı principal namespace'inde `link_account` çağırır;
  daha önce disconnect edilmiş aynı customer satırını kimliğini değiştirmeden yeniden active yapar.

### Campaign performance read sözleşmesi

- Sabit GAQL allowlist'i yalnız `segments.date`, `campaign.id/name/status` ve
  `metrics.impressions/clicks/cost_micros/conversions` alanlarını seçer; dışarıdan ham GAQL veya alan adı
  kabul edilmez. Tarih aralığı iki uç dahil en fazla 90 gündür.
- ID JSON'da string, `cost_micros` kayıpsız integer, conversions sayı, enum resmi proto adı olarak taşınır;
  gelecekte gelen `UNKNOWN` değeri korunur. Provider'ın bulunmayan string scalar varsayılanları (`date`,
  `campaign_name`) `null`; numeric varsayılanları `0`, enum varsayılanı `UNSPECIFIED` olarak eşlenir.
- Her çağrı tek provider sayfası döndürür ve devamı varsa opaque `next_page_token` verir; istemci token
  verilmeden sonraki sayfayı çekmez. Google Ads v24 `Search` sabit 10.000 satır sayfası kullandığı ve
  `page_size` gönderimini `PAGE_SIZE_NOT_SUPPORTED` ile reddettiği için RPC'ye `page_size` iletilmez.
  Uygulama-seviyesi row/byte sınırı ve principal/customer/query bağlı cursor Faz 5.5 kapsamındadır.
- Tool çağrısından önce active `(principal_id, customer_id)` ownership ve principal'a ait credential
  yeniden doğrulanır. Kota ve timeout retryable; auth hatası retryable değildir ve hiçbir hata secret döndürmez.

### Ad group performance read sözleşmesi

- Sabit GAQL allowlist'i yalnız `segments.date`, `campaign.id`, `ad_group.id/name/status` ve
  `metrics.impressions/clicks/cost_micros/conversions` alanlarını `ad_group` kaynağından seçer. Dışarıdan
  ham GAQL/alan adı kabul edilmez ve ortak doğrulanmış tarih penceresi kullanılır.
- Campaign read ile aynı tek-sayfa/opaque continuation, integer micros, proto enum adı ve hata/retry
  davranışını taşır. Eksik `date`/`ad_group_name` string scalar'ları `null`, numeric scalar'lar `0`, enum
  varsayılanı `UNSPECIFIED` olarak eşlenir; `UNKNOWN` kayıpsız korunur.
- Active account ownership kapısı seçilen hesabın kaydedilmiş `login_customer_id` değerini credential'a
  bağlar. Manager üzerinden erişimde bu değer resmî Google Ads client konfigürasyonuna aynen aktarılır;
  doğrudan erişilen hesapta alan hiç gönderilmez.

### Keyword performance read sözleşmesi

- Sabit GAQL allowlist'i `keyword_view` kaynağından yalnız `segments.date`, campaign/ad group/criterion
  ID'leri, `ad_group_criterion.keyword.text/match_type`, criterion status ve dört asgari metriği seçer.
  Ham GAQL veya dinamik alan adı kabul edilmez.
- Match type (`BROAD`/`EXACT`/`PHRASE`) ve status resmî proto enum adıyla taşınır; response-only
  `UNKNOWN` kayıpsız korunur. Eksik keyword text/date `null`, numeric scalar'lar `0`, enum varsayılanları
  `UNSPECIFIED` olarak eşlenir; micros integer kalır.
- Keyword/ad metni güvenilmeyen dış veridir: komut, rol veya tool argümanı olarak ayrıştırılmaz; yalnız
  allowlist içindeki `keyword_text` alanında verbatim döner. İçeriği customer/principal kapsamını,
  yapılandırılmış tool argümanlarını veya insan-onayı durumunu değiştiremez.
- Campaign/ad group ile aynı ownership, manager login-customer, caller-paced continuation ve
  quota/timeout/auth hata sözleşmesini kullanır.

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
- Public MCP dışında ayrı kullanıcı-facing HTTP API yayınlanıp yayınlanmayacağı.

## Güncelleme geçmişi

- 2026-07-22 — Keyword performance allowlist/eşleme sözleşmesi tamamlandı; match type/status,
  success/empty/multi-page/micros/enum/null/quota/timeout/auth, principal ownership ve injection-benzeri
  metnin adapter + gerçek MCP üzerinden opak veri kalması contract testleriyle sabitlendi.

- 2026-07-22 — Ad group performance allowlist/eşleme sözleşmesi campaign standardıyla hizalandı;
  manager/direct `login_customer_id`, success/empty/multi-page/micros/enum/null/quota/timeout/auth ve
  ortak principal ownership kapısı contract testleriyle sabitlendi.

- 2026-07-22 — Campaign performance allowlist/eşleme sözleşmesi tamamlandı; v24'te kaldırılmış
  `page_size` RPC parametresi çıkarıldı ve success/empty/multi-page/micros/enum/null/quota/timeout/auth
  resmi response/exception contract testleriyle sabitlendi. Ownership mevcut principal-scoped credential
  contract testleriyle birlikte doğrulandı.

- 2026-07-22 — Faz 5.1 accessible-account adapter'ı eklendi: doğrudan customer listesi, manager
  `customer_client` hiyerarşisi, `login_customer_id` eşlemesi, ID doğrulama/deduplikasyon ve
  principal-scoped re-link sözleşmesi uygulanıp mock contract testlerine bağlandı.

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
