# Hata yönetimi ve yeniden deneme

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
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

## Açık sorular

- Servis bazlı retry sayısı/toplam süre ve UI bekleme eşikleri.
- Execution reconciliation job periyodu ve manuel müdahale SLA'sı.
- İlk allowlist operasyonlarda partial failure'a gerçekten ihtiyaç olup olmadığı.
- Anthropic SDK hata sınıflarının retry matrisi (SDK sürümü seçilince doğrulanacak).

## Güncelleme geçmişi

- 2026-07-17 — Başlangıç audit'i yazılamayan execution'ın `pending` kalmayıp provider çağrısız
  `failed` olması ve idempotent tekrarın mutate etmemesi netleştirildi.
- 2026-07-17 — Hata taksonomisi, merkezi retry bütçesi, unknown mutate ve partial failure politikası tanımlandı.
