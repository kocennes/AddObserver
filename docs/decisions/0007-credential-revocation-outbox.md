# ADR-0007: Credential revocation için transactional outbox

- Durum: Kabul edildi
- Tarih: 2026-07-19
- Sahip: Ürün sahibi onayıyla ajan (Codex)

## Bağlam

Disconnect, connector DB yetkilerini ve secrets manager içindeki Google refresh token'ını birlikte iptal
etmelidir. DB-first akış vault silme hatasında tekrar bulunamayan bir secret bırakır; vault-first akış ise
eşzamanlı relink sırasında yanlış credential yaşam döngüsünü etkileyebilir. DB ve harici vault arasında ortak
transaction yoktur.

## Karar

- Aynı DB transaction'ı aktif credential metadata'sını revoke eder ve credential başına benzersiz bir
  `credential_revocation_job` kaydı oluşturur.
- İş `principal_id + credential_id + vault_ref` snapshot'ını taşır; secret değeri asla DB'ye girmez.
- Principal ownership composite FK ve FORCE RLS ile korunur.
- Worker vault revoke'u transaction dışında yapar. Başarısız deneme güvenli hata kodu, attempt sayısı ve
  sonraki deneme zamanıyla kalıcı kalır; başarı `completed` olur. Raw provider hatası saklanmaz.
- Route wiring, atomik enqueue repository'si ve retry/claim worker'ı test edilmeden production'da açılmaz.

## Sonuçlar

Disconnect kullanıcı erişimini DB'de hemen kapatabilir; vault silme eventually-consistent fakat durable ve
retry edilebilir olur. Migration yalnız outbox temelini kurar; worker lifecycle bu ADR'nin sonraki artışıdır.
