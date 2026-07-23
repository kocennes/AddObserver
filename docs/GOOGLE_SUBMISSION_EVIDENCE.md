# Google Ads ve OAuth submission kanıt paketi

**Durum:** Taslak — gönderim, kullanıcı onayı ve production kimliği bekleniyor  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Değişmez ürün beyanı

AddObserver, dış kullanıcılara açık, ücretsiz bir Claude Google Ads connector'üdür. Her kullanıcı kendi OAuth
izniyle hesabını bağlar. Reporting verisini okur; dar yönetim önerileri hazırlayabilir; açık insan onayı olmadan
mutate yapmaz ve yeni kampanyayı `PAUSED` oluşturur. Ödeme bilgisi toplamaz. Token kullanıcılar arasında paylaşılmaz.

## 11.7 Developer token başvuru paketi

| Kanıt | Repository kaynağı | Durum |
|---|---|---|
| Ürün modeli, ücretsiz fiyat ve dış kullanıcı akışı | `README.md`, `docs/PRODUCT.md`, bu belge | Hazır |
| OAuth, token izolasyonu, approval/write güvenliği | `docs/AUTH.md`, `docs/SECURITY.md`, auth/approval testleri | Repo kanıtı hazır |
| Reporting + dar management permissible-use beyanı | `docs/API_CONTRACTS.md`, `docs/MCP.md` | Hazır; Google sınıflandırması bekliyor |
| Veri akışı/privacy/retention | `docs/PRODUCTION_DATA_INVENTORY.md`, `PRIVACY_POLICY.md` | Privacy ve retention hukuk onayı bekliyor |
| RMF matrisi | `docs/GOOGLE_API_ACCESS.md` ve aşağıdaki matris | Google sınıflandırması bekliyor |
| Reviewer/test hesabı ve canlı URL | Production ortamı | Bekliyor; gerçek müşteri hesabı kullanılmaz |
| Manager account/API Center, contact ve başvuru sahibi | Ürün sahibi | Bekliyor |
| Gönderim onayı, submission ID/tarih ve Google sonucu | Ürün sahibi + API Center | Gönderilmedi; açık onay zorunlu |

## 11.8 OAuth verification paketi

| Gereksinim | Kanıt / eksik |
|---|---|
| Production Google Cloud project ve doğru contact/owner | Bekliyor |
| Verified domain, açıklayıcı homepage, aynı-domain Privacy ve Terms URL'leri | Bekliyor |
| Support email ve uygulama adı/marka tutarlılığı | Bekliyor |
| `https://www.googleapis.com/auth/adwords` gerekçesi | Reporting ve yalnız kullanıcı-onaylı Ads yönetimi için gerekli; daha dar scope aynı işlevi sunmuyor. Console sınıflandırması doğrulanacak. |
| Consent ekranı ve affirmative consent | Akış repo düzeyinde mevcut; production ekran görüntüsü/video bekliyor |
| Demo video | Login → OAuth consent → account select → report → proposal → approve/reject → disconnect/revoke; secret/müşteri verisi göstermeyen test hesabıyla kaydedilecek |
| Test talimatları | Reviewer hesabı, test Ads hesabı, read senaryosu, approval ve disconnect adımları production sonrası eklenecek |
| Privacy disclosures / Limited Use | `PRIVACY_POLICY.md` hukuk onayı bekliyor |
| Security assessment | Restricted-scope sınıfı Console/Google ile doğrulanacak; Google çağırdığında yetkili CASA assessor ve yıllık yenileme planlanacak |
| Gönderim onayı/sonuç | Gönderilmedi; ürün sahibinin açık onayı zorunlu |

## 11.9 Geçici RMF kanıt matrisi

RMF yalnız Standard Access ve Google'ın tool kullanım sınıflandırmasına göre kapatılır. Yazılı Google cevabı olmadan
hiçbir satır `N/A` veya compliant sayılmaz.

| Yetenek grubu | Mevcut kanıt | Durum |
|---|---|---|
| Reporting / hesap-kampanya performansı | `api/reporting.py`, `api/queries.py`, HTTP/MCP ve pagination testleri | Uygulanan dar reporting; Google RMF eşlemesi bekliyor |
| Account discovery ve principal ownership | `api/accounts.py`, repository izolasyon testleri | Uygulandı |
| Campaign management önerisi | approval proposal şeması ve testleri | Hazırlama/onay kapısı uygulandı; kapsam dar |
| Mutate execution | Onaysız yazma negatif testleri ve audit modeli | Güvenlik sözleşmesi mevcut; Google RMF fonksiyon listesi bekliyor |
| Campaign/ad group/ad/keyword creation-management UI'ı | Kapsamda tamamlanmış değil | Full-service denirse bloklayıcı |
| Recommendation apply/dismiss | İlk kapsam dışında | Google yazılı `N/A` demeden kapatılamaz |
| Keyword planning/research | İlk kapsam dışında | Permissible-use genişletilmeden açılamaz |
| Billing/payments | Ürün ödeme almaz; Ads billing yönetimi sunmaz | Google yazılı sınıflandırması bekliyor |
| Accessibility ve reviewer UX | `docs/DESIGN.md`; production ekran kanıtı yok | Bloklayıcı kanıt eksik |

## Gönderim prosedürü

1. Tüm `Bekliyor/Bloklayıcı` satırları kapanır ve paket secret/gerçek müşteri verisi içermediği doğrulanır.
2. Ürün sahibine gönderilecek form değerleri ve dış etkiler gösterilir; açık yazılı onay alınır.
3. Yetkili kişi Google Console/API Center'da gönderir. Ajan otomatik gönderim yapmaz.
4. Submission ID, tarih, tam beyan sürümü, Google yazışması, access/permissible-use ve koşullar
   `GOOGLE_API_ACCESS.md` değişiklik geçmişine işlenir.

## Güncel resmi kaynaklar

- [Google Ads access levels ve permissible use](https://developers.google.com/google-ads/api/docs/api-policy/access-levels)
- [Required Minimum Functionality](https://developers.google.com/google-ads/api/docs/api-policy/rmf)
- [Developer token](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)
- [OAuth verification requirements](https://support.google.com/cloud/answer/13464321)
- [OAuth verification submission](https://support.google.com/cloud/answer/13461325)
- [Restricted-scope security assessment](https://support.google.com/cloud/answer/13465431)

## Değişiklik geçmişi

- 2026-07-22 — Faz 13.3 doğrulaması: bu belgede ve `docs/SECURITY.md`'de daha önce kaydedilmiş olan
  "Security assessment ... Google çağırdığında yetkili CASA assessor" satırı yeniden kontrol edildi —
  Google'ın veya bağımsız bir değerlendiricinin ne zorunlu bir assessment/pentest talebi ne de bir
  bulgu raporu şu ana kadar var; triaj edilecek gerçek bir bulgu yok. Bu yüzden `todo.md` 13.3 "kapatma"
  değil, "henüz açılmadı" durumundadır — assessment talebi Google'ın restricted-scope OAuth verification
  sürecini (11.8) tetiklediğinde gelir. Risk acceptance/triaj süreci ürün sahibi + güvenlik/hukuk onayı
  şartıyla zaten `docs/SECURITY.md` "Olay müdahalesi ve kalite kapıları"nda tanımlı; yeni bir karar
  uydurulmadı.
- 2026-07-22 — Faz 11.7–11.9 başvuru paketi, video/test senaryosu ve geçici RMF kanıt matrisi oluşturuldu.
