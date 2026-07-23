# Hata yönetimi ve yeniden deneme

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-18
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Google Ads, Anthropic, DB, queue ve iç API hatalarını doğru sınıflandırmak; güvenli kullanıcı mesajı,
retry, idempotency ve belirsiz yazma sonucu davranışını belirlemek.

## Araştırma

- Google Ads [Error Types](https://developers.google.com/google-ads/api/docs/best-practices/error-types)
  hataları authentication, retryable, validation ve sync-related olarak sınıflandırır; yalnız geçici
  hatalarda exponential backoff önerir.
- [Handle API errors](https://developers.google.com/google-ads/api/docs/get-started/handle-errors), hata kodu,
  message, trigger/location ve destek için `request_id` yakalanmasını ister.
- [Partial Failure](https://developers.google.com/google-ads/api/docs/best-practices/partial-failures), bazı
  servislerin başarılı operasyonları commit edip hatalı olanları ayrı döndürebildiğini, bağımlı operasyonlarda
  bu modun kullanılmaması gerektiğini açıklar.
- [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) makinece okunur HTTP problem formatını ve güvenli
  detay ilkesini tanımlar.

## Karar

| Sınıf | Örnek | Davranış | Otomatik retry |
|---|---|---|---|
| Validation/policy | yanlış alan, Ads policy | Kullanıcıya alan/kod; proposal `failed` | Hayır |
| Auth | `invalid_grant`, permission | Credential pasifleştir, işleri durdur | Hayır |
| Rate/quota | `RESOURCE_*EXHAUSTED` | Queue/backoff, görünür gecikme | Evet, bütçeli |
| Transient | timeout, 5xx, unavailable | Jitter'lı exponential backoff | Read için evet |
| Sync/stale | resource değişmiş/silinmiş | Yeniden oku, proposal `stale` | Kör retry yok |
| Internal invariant | principal/account/audit/hash hatası | Fail closed, güvenlik alarmı | Hayır |

- Retry policy merkezi adapter'dadır; çağıran katman kendi döngüsünü ekleyemez. Maksimum deneme + toplam
  elapsed-time bütçesi vardır; Google `retry_delay`/HTTP `Retry-After` alt sınır olarak uygulanır.
- Backoff full jitter kullanır. Validation, auth, permission ve business-rule hataları retry edilmez.
- Read çağrıları güvenli biçimde retry edilebilir. Mutate timeout/bağlantı kopması sonucu **unknown** ise aynı
  operasyon körlemesine tekrarlanmaz; önce resource güncel durumu okunur ve idempotency/execution kaydı uzlaştırılır.
- Partial failure varsayılan kapalıdır. Yalnız birbirinden bağımsız, tek tek audit/onaylanmış operasyonlarda
  açılır; her indeks ayrı success/failure execution sonucu üretir.
- DB/audit başlangıç kaydı başarısızsa Google mutate yapılmaz ve provider'a hiç ulaşmayan
  execution deterministik olarak `failed` işaretlenir; aynı idempotency anahtarı mutate denemeden bu sonucu döndürür.
  Google başarılı fakat sonuç kaydı başarısızsa
  execution `unknown` kabul edilir ve reconciliation alarmı oluşur.
- Anthropic invalid schema cevabı en fazla bir kontrollü repair denemesi alır; sonra analiz başarısızdır.
- Kullanıcı hatası RFC 9457 ile güvenli ve eyleme dönük; teknik log correlation + provider request ID içerir.
  Token, tam payload, stack trace ve diğer kullanıcı/hesap bilgisi kullanıcıya/loga verilmez.
- **"Auth" satırının uygulaması** (`mcp/tools.py::_fetch_report_page`,
  `mcp/credentials.py::deactivate_credential_on_auth_failure`): reporting tool çağrısı
  sırasında `ErrorClass.AUTH` sınıfında bir `AdsApiError` (revoked/expired refresh
  token, `TWO_STEP_VERIFICATION_NOT_ENROLLED`, izin iptali -- `authentication_error`/
  `authorization_error` alanlarının tamamı, ADR ile daraltılmadı) yakalanırsa
  `OAuthCredentialRepository.revoke_active` çağrılır. Bu yalnız DB satırını pasifleştirir
  (`docs/SECURITY.md` "pasifleştirilir"); vault sırrını yok eden disconnect'ten farklı
  olarak geri döndürülebilir bir duraklatmadır. Sonraki her çağrı Google'a hiç
  ulaşmadan `mcp/credentials.py::resolve_google_ads_credentials`'ın
  `no_active_google_credential` dalına düşer -- bu, "sonsuz retry yapma" gereksinimini
  bir sonraki çağrının kendisini otomatik başarısız kılarak karşılar. Kanıt:
  `backend/tests/test_mcp_credentials.py::DeactivateCredentialOnAuthFailureTests`,
  `backend/tests/test_mcp_integration.py::test_auth_class_tool_failure_deactivates_the_credential`
  (gerçek MCP tool-call zinciri üzerinden).

## Açık sorular

- Anthropic SDK hata sınıflarının retry matrisi (SDK sürümü seçilince doğrulanacak).

## Güncelleme geçmişi

- 2026-07-22 — Faz 5.6 kapandı: `todo.md` 9.1'in eklediği yapılandırılmış JSON logging
  (`observability/logging.py::JsonEventLogger`) artık bu maddenin tek eksik parçasıydı --
  `mcp/tools.py::_fetch_report_page`/`sync_accessible_accounts`'ın gerçek bir Google Ads
  `AdsApiError`'ı yakaladığı iki noktaya (`_log_google_ads_failure`) bağlandı: her
  Google Ads-kaynaklı hata `operation` (`google_ads_<rapor>_report` /
  `google_ads_account_discovery`), `reason_code` (sınıflandırıcının `code`'u),
  principal/customer pseudonymous referansı ve Google'ın kendi `request_id`'siyle
  (yeni `JsonEventLogger.emit(google_request_id=...)` alanı, yalnız güvenli
  karakter kümesiyle eşleşirse taşınır, aksi halde tamamen atlanır) tek bir olay
  olarak kaydediliyor. `AdsApiError.message` (Google'ın kendi serbest metni) bilinçli
  olarak sabit şemaya eklenmedi -- yalnız kod/`request_id` audit/telemetry'ye taşınır.
  Bizim kendi ürettiğimiz `AdsApiError`'lar (rate-limit/invalid_date/invalid_page_token
  gibi, `request_id=None`) bu logu tetiklemez -- yalnız gerçek Google Ads/transport
  kaynaklı hatalar loglanır. Kanıt:
  `backend/tests/test_observability.py::test_google_request_id_is_carried_when_present_and_safe`/
  `test_google_request_id_is_omitted_when_absent_or_unsafe`,
  `backend/tests/test_mcp_integration.py::test_google_ads_failure_logs_the_google_request_id`
  (gerçek MCP tool-call zinciri + gerçek `GoogleAdsException` üzerinden uçtan uca).
  Doğrulama: `python -m unittest discover -s backend/tests` (553 test, OK), `pyright
  backend/src` (0 hata), `ruff check .`/`ruff format --check .` (temiz), `bandit -c
  backend/pyproject.toml -r backend/src` (0 bulgu), `python tools/check_docs.py`
  (27 belge doğrulandı), `git diff --check` (yalnız CRLF normalizasyon uyarıları).
  Commit/push yapılmadı.
- 2026-07-22 — Faz 6.10 bütçeleri kabul edildi: Google Ads read `4 attempt/30s`, `0.5s` taban ve
  `8s` tavan full-jitter; DB transaction `1 attempt/5s` (serialization/deadlock ancak bütün transaction
  idempotentse en çok 2); dış auth/discovery HTTP `3 attempt/15s`; backend model çağrısı v1'de olmadığı
  için Anthropic runtime bütçesi yoktur. UI 2 saniyeden sonra bekleme durumu gösterir, 10 saniyede async
  iş gerektirir. Partial failure Faz 8 allowlist'inde ihtiyaç kanıtlanana kadar kapalıdır. Unknown execution
  5 dakikada bir reconcile edilir; 15 dakika sonunda manual review alarmı, 4 saat hedef SLA uygulanır.
  Belirsiz mutate hiçbir durumda kör retry edilmez.

- 2026-07-22 — Faz 5.6: karar tablosunun altı sınıfının tamamı (permission dahil --
  `authorization_error` alanı `authentication_error`'dan ayrı fakat aynı AUTH sınıfına
  düşer -- 2SV `TWO_STEP_VERIFICATION_NOT_ENROLLED`, boş/`unknown` failure govdesi)
  gerçek Google Ads v24 proto hata tipleriyle `backend/tests/test_api_errors.py`'de
  doğrulandı; sınıflandırıcı kodunda değişiklik gerekmedi (alan-adı bazlı eşleme zaten
  her `authentication_error`/`authorization_error` değerini kapsıyordu), yalnız eksik
  regresyon testleri eklendi. Google `request_id` her iki sınıflandırma yolunda da
  (`classify_google_ads_exception`, boş-failure dalı dahil) yakalanıyor ve hiçbir
  secret/payload sızdırmıyor (bkz. `test_message_never_leaks_beyond_googles_own_text`,
  `test_unrecognised_exception_text_never_reaches_the_public_message`); ancak bunu
  gerçek bir audit/telemetry kaydına yazacak yapısal loglama henüz yok (`todo.md` 9.1
  hâlâ açık) -- madde bu tek eksik nedeniyle tamamlanmış sayılmadı, kod hazır olduğunda
  9.1 bu alanı bağlayacak.
- 2026-07-18 — Faz 3.6: "Auth" satırının "Credential pasifleştir, işleri durdur" kararı
  ilk kez koda bağlandı (`mcp/tools.py`, `mcp/credentials.py`) -- önceden karar
  belgede vardı ama hiçbir çağrı yolu bunu tetiklemiyordu; bir AUTH-class hata yalnız
  tek isteği başarısız kılıyor, credential DB'de aktif kalmaya devam ediyordu. Ayrıca
  `docs/AUTH.md`'de ayrı bir "scope denial" kusuru bulundu ve düzeltildi (Google'ın
  çoklu-scope onay ekranında kullanıcı `adwords`'ü reddedip diğerlerini kabul ederse
  callback yine de başarılı bir `code` ile döner; bu artık `access_denied` olarak
  ele alınır ve çalışmayan bir credential hiç kalıcı hale getirilmez).
- 2026-07-17 — Başlangıç audit'i yazılamayan execution'ın `pending` kalmayıp provider çağrısız
  `failed` olması ve idempotent tekrarın mutate etmemesi netleştirildi.
- 2026-07-17 — Hata taksonomisi, merkezi retry bütçesi, unknown mutate ve partial failure politikası tanımlandı.
