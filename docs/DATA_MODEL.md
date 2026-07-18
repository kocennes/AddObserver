# Veri modeli

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18

## Amaç

Public connector kullanıcıları, Google credential/account eşlemeleri, öneri/onay/uygulama ve audit verisinin
mantıksal varlıklarını ve yaşam döngüsünü tanımlamak.

## Araştırma

- [OWASP Multi-Tenant Security](https://cheatsheetseries.owasp.org/cheatsheets/Multi_Tenant_Security_Cheat_Sheet.html)
  kimlikten türetilen tenant/principal bağlamı ve her veri katmanında ownership doğrulaması önerir.
- Google [OAuth best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
  token'ın güvenli saklanmasını, revoke ve artık gerekmediğinde kalıcı silmeyi gerektirir.

## Karar
**Sonraki gözden geçirme:** 2026-10-17

## Modelleme kuralları

- Kullanıcıya ait iş tablolarının tamamı connector `principal_id` taşır; Google Ads varlıkları ayrıca
  `customer_id` taşır. Principal ID, Google customer ID veya email ile aynı değildir.
- Dış kimlikler tahmin edilemez UUID/ULID olur; Google resource name ayrıca saklanabilir.
- UTC `created_at`/`updated_at`; kullanıcıya gösterimde açık timezone kullanılır.
- Para Google Ads ile uyumlu tamsayı `amount_micros` + ISO para birimi olarak saklanır; float kullanılmaz.
- OAuth token değeri DB'de tutulmaz; yalnız secrets manager referansı ve yaşam döngüsü metadata'sı bulunur.
- Audit olayları append-only'dir; soft delete audit yerine geçmez.

## Çekirdek varlıklar

| Varlık | Zorunlu alanlar | Temel kısıt |
|---|---|---|
| `principal` | `id`, `issuer`, `subject`, `status` | `(issuer, subject)` benzersiz; izolasyon kökü |
| `oauth_client_grant` | `principal_id`, `client_id`, `scopes`, `status` | Connector consent; Google token değildir |
| `ads_account` | `principal_id`, `customer_id`, `login_customer_id`, `status` | `(principal_id, customer_id)` benzersiz |
| `oauth_credential` | `principal_id`, `vault_ref`, `status`, `key_version` | Google secret değeri yok |
| `analysis_run` | `principal_id`, `customer_id`, `window`, `input_snapshot_hash`, `status` | Varsayılan kısa ömürlü |
| `proposal` | `principal_id`, `customer_id`, `type`, `payload`, `proposal_hash`, `risk`, `status`, `expires_at` | Allowlist şema |
| `approval` | `proposal_id`, `approver_id`, `decision`, `proposal_hash`, `decided_at` | Karar değiştirilemez |
| `execution` | `proposal_id`, `idempotency_key`, `before`, `after`, `google_request_id`, `status` | Onay hash'i eşleşir |
| `audit_event` | `event_id`, `occurred_at`, `actor`, `principal_id`, `customer_id`, `event_type`, `proposal_id`/`approval_id`/`execution_id`, `outcome`, `reason_code`, `correlation_id`, `google_request_id` | Append-only |
| `web_login_state` | `state_hash`, `status`, `expires_at` | Tek kullanımlık; `/approvals` girişi için, Google credential'dan bağımsız |
| `web_session` | `token_hash`, `principal_id`, `csrf_token_hash`, `expires_at`, `revoked_at` | `/approvals` tarayıcı oturumu; connector access token'dan ayrı bir düzlem (docs/AUTH.md) |

## Durum makineleri

- Proposal: `draft → pending_approval → approved|rejected|expired → executing → applied|failed|stale`.
- Credential: `pending → active → revoked|invalid`.
- Ads account: `active → disconnected` (principal'ın kendi disconnect isteğiyle; bkz. `docs/AUTH.md`
  ve `backend/src/auth/disconnect.py`). Satır silinmez — `proposal`/`approval`/`audit_event` geçmişi
  `customer_id`'ye referans vermeye devam eder.
- Execution yalnız `approved` öneriden başlar. `proposal_hash` veya mevcut Google değeri değişirse `stale` olur.
- İnsan onay/red kararı `approval` satırıyla birlikte `approval.decided` audit_event'i üretir;
  audit kaydı `proposal_id`, `approval_id`, `principal_id`, `customer_id` ve correlation ID taşır.

## İzolasyon ve bütünlük

- Foreign key'ler mümkün olduğunda principal'ı da içerir; kullanıcılar arası ilişki DB seviyesinde reddedilir.
- PostgreSQL seçilirse RLS savunma katmanı olarak değerlendirilir; uygulama filtresinin yerine geçmez.
- Unique constraint ve idempotency key aynı onayın iki kez uygulanmasını engeller.
- Proposal input/output JSON'u sürümlü şemaya göre doğrulanır; serbest JSON kalıcı sözleşme değildir.
  `rationale` en fazla 2000 karakter ve kontrol karakteri içermez; `current_status` yalnız Google Ads'in
  gerçek `CampaignStatus` değerlerinden (`ENABLED`/`PAUSED`/`REMOVED`) biri olabilir; `campaign_id` en fazla
  19 haneli (`int64`) olabilir (`backend/src/approval/payload_schema.py`).

## Retention ve sınıflandırma

- **Secret:** kasada; minimum erişim, rotasyon ve revoke.
- **Müşteri reklam/performance verisi:** confidential; iş ihtiyacı kadar saklanır (`TBD`).
- **Audit:** restricted; yasal/iş retention kararı `TBD`, bütünlük korumalı.
- **Uygulama logu:** hassas içerik redacted; operasyon ihtiyacı kadar (`TBD`).

Şema/migration uygulamasından önce DB motoru, RLS yaklaşımı ve retention kararları ADR ile kabul edilir.

## Açık sorular

- Kesin retention süreleri ve audit WORM hedefi `LEGAL.md`/`OBSERVABILITY.md` kararına bağlıdır.
- Principal merge/account recovery davranışı.

## Güncelleme geçmişi

- 2026-07-18 — Proposal payload'ında `rationale` (uzunluk + kontrol karakteri), `current_status`
  (Google Ads `CampaignStatus` allowlist'i) ve `campaign_id` (19 hane üst sınırı) için sınır
  değer doğrulaması eklendi; önceden bu alanlar sınırsız serbest metindi.
- 2026-07-18 — `authorization_transaction`'a `consent_csrf_hash` alanı eklendi (docs/AUTH.md
  "Account-linking CSRF savunması"); değer yalnız SHA-256 hash'i olarak saklanır, mevcut
  token/kod hash-at-rest deseniyle aynıdır.
- 2026-07-18 — `web_session` CSRF alanı hash-at-rest sözleşmesine göre `csrf_token_hash`
  olarak netleştirildi (docs/AUTH.md, docs/SECURITY.md).
- 2026-07-17 — `/approvals` insan onay yüzeyi için `web_login_state`/`web_session` varlıkları
  eklendi (docs/AUTH.md, docs/ARCHITECTURE.md).
- 2026-07-17 — İnsan onay/red kararlarının `approval.decided` audit_event'i ile atomik yazılması
  ve audit `approval_id` bağının kullanılması netleştirildi.
- 2026-07-17 — Execution audit olaylarına uzlaştırma ve uçtan uca izlenebilirlik için
  `execution_id` alanı eklendi.
- 2026-07-17 — İzolasyon kökü ajans tenant'ından public connector principal'ına çevrildi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi.
