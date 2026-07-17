# Google Ads API erişimi ve RMF uyumu

**Durum:** Taslak — Google Ads API Compliance sınıflandırması bekleniyor  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Herkese açık Google Ads connector'ünün developer token erişim seviyesini, permissible use kapsamını,
Required Minimum Functionality (RMF) yükümlülüklerini ve OAuth doğrulama ön koşullarını belirlemek.

## Araştırma

- Google Ads [Access Levels and Permissible Use](https://developers.google.com/google-ads/api/docs/api-policy/access-levels),
  Explorer/Basic/Standard seviyelerini ayırır. Basic üretim hesaplarında kayan 24 saatte 15.000 operation;
  Standard çoğu servis için günlük operation sınırı olmadan çalışır ancak sistem/servis rate limitleri sürer.
- [Developer Token](https://developers.google.com/google-ads/api/docs/api-policy/developer-token) başvurusu bir
  Google Ads manager account API Center üzerinden yapılır; tool geliştiricisi kendi developer token'ını alır.
- [Required Minimum Functionality](https://developers.google.com/google-ads/api/docs/api-policy/rmf) yalnız
  Standard Access token'larına uygulanır. Full-service tool için creation+management+reporting; reporting-only
  için görüntülenen hiyerarşi seviyelerinin reporting gereksinimleri uygulanır. Sınırlı/specialized tool'un
  full-service sayılıp sayılmayacağına Compliance ekibi karar verir.
- [API Terms and Conditions](https://developers.google.com/google-ads/api/terms) ile
  [API Policy](https://developers.google.com/google-ads/api/docs/api-policy/overview) developer token kullanımı,
  veri kullanımı ve denetime tabi uyumun bağlayıcı kaynaklarıdır.
- Google [OAuth verification](https://support.google.com/cloud/answer/13464321), public uygulamada doğrulanmış
  domain, işlevi açıklayan homepage, aynı domain'de privacy policy, support bilgisi, en dar scope, scope
  justification ve gerekirse demo video ister. Scope sınıfı Cloud Console'da kesinleştirilir.
- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
  Google kullanıcı verisinin erişim/kullanım/saklama/paylaşım/silme biçiminin privacy policy'de açıkça
  belirtilmesini ve bildirilen amaçlarla sınırlı kalmasını şart koşar.
- Google Ads'in [credential güvenliği](https://developers.google.com/google-ads/api/docs/productionize/secure-credentials)
  belgesi `https://www.googleapis.com/auth/adwords` scope'unu açıkça **restricted** olarak sınıflandırır;
  production öncesi OAuth app verification tamamlanmalı, developer token parola gibi korunmalı ve çok
  kullanıcılı uygulamada her kullanıcının token'ı güvenli biçimde saklanmalıdır.
- Google Ads [API politikası](https://support.google.com/adspolicy/answer/6169371), API'nin yalnız kampanya
  creation/management/reporting için ve developer-token başvurusunda açıklanan şekilde kullanılmasına izin
  verir. Reporting araca management eklemek gibi amaç değişiklikleri public edilmeden önce API Tool Change Form
  ile bildirilir. Google uyum incelemesi yapabilir; güncel API Center iletişim bilgisi, istekten itibaren yedi
  gün içinde canlı işlevle eşdeğer demo ve son kullanıcı kötüye kullanımını önleyen authenticated erişim gerekir.
- [Google Ads API Terms](https://developers.google.com/google-ads/api/docs/api-policy/terms), Google'ın client
  arayüzünü inceleyebilmesine ve API faaliyetini audit etmesine izin verir; uygulanabilir politika ihlalleri
  quota düşürme, token sonlandırma ve non-compliance fee sonucu doğurabilir.
- Google'ın [OAuth app değişiklikleri](https://support.google.com/cloud/answer/13464018) rehberine göre ad,
  logo, redirect URI, homepage veya privacy-policy URL değişikliği brand re-verification; yeni scope ise scope
  verification gerektirebilir. Yeni scope doğrulanmadan production kodunda istenmez.

## Karar

### Aşamalı erişim

1. Geliştirme yalnız Test Account Access/Google Ads test account + mock ile başlar.
2. Kontrollü public beta için mevcut en uygun üretim seviyesi alınır; Basic'in 15.000 operation bütçesi
   `RATE_LIMITS.md` ile sert biçimde korunur ve kullanıcı sayısı buna göre sınırlandırılır.
3. Genel directory lansmanı için Standard Access başvurusu hedeflenir. “Ücretsiz” olmak quota/policy
   yükümlülüklerini kaldırmaz.

### Permissible use ve RMF

- Ürün reporting + dar proposal/onaylı management sunacağı için başvuruda kullanım eksiksiz açıklanır;
  reporting-only olarak yanlış beyan edilmez.
- İlk tool seti special-purpose olarak dar tutulur. Google Ads API Compliance ekibinden yazılı olarak şu
  sınıflandırma istenir: full-service mi, special-purpose management tool mu; hangi creation/management/
  reporting RMF satırları uygulanıyor?
- Bu yanıt gelmeden “RMF compliant” iddiası veya public GA yapılmaz. Full-service sınıflandırılırsa bütün
  ilgili RMF backlog'u ve UI erişilebilirliği tamamlanmadan Standard üretim lansmanı yapılmaz.
- RecommendationService önerileri gösterilirse “Google Ads Recommendations” olarak açıkça etiketlenir;
  apply/dismiss kapsamı RMF sınıflandırması onaylanmadan eklenmez.
- KeywordPlan servisleri, geniş RMF etkisi nedeniyle ilk faz dışında tutulur.

### OAuth verification paketi

- Yetkili domain üzerinde homepage, `PRIVACY_POLICY.md`, `TERMS.md`, support ve veri silme/revoke akışı
  yayınlanır; OAuth consent screen ile isim/logo/domain/linkler birebir tutarlı olur.
- Yalnız `https://www.googleapis.com/auth/adwords` istenir. Scope restricted olduğundan brand + restricted
  scope verification tamamlanır. Restricted verinin server-side saklanması/iletilmesi nedeniyle Google'ın
  isteyeceği bağımsız güvenlik değerlendirmesi ve yıllık yeniden değerlendirme bütçe/takvime alınır; kesin
  kapsam Verification Center sonucu ve hukuk/güvenlik incelemesiyle kaydedilir.
- Scope justification ve demo video; connect→veri okuma→öneri→açık kullanıcı onayı→write→disconnect/delete
  akışını gösterir. AI eğitimi için Google user data kullanılmaz.
- OAuth projesi, developer token, MCC ve production iletişim adreslerinin sahipliği güncel tutulur.

### Sürekli politika uyumu

- Developer-token başvurusundaki ürün kapsamı versionlanır. Reporting'e write/management, yeni servis veya
  belirgin kullanım değişikliği eklenmeden önce Tool Change Form/Compliance onayı kanıtlanır.
- API Center iletişim adresi ekip alias'ı olur ve düzenli test edilir. Google'ın uyum talebine cevap SLA'sı
  en fazla 2 iş günü; istenen demo hesap en geç 7 gün içinde sağlanır.
- Her son kullanıcı authenticated olur; principal/customer düzeyinde abuse detection, rate limit ve askıya
  alma uygulanır. Dolaylı kullanıcının ihlali developer token'ın iptaline yol açabileceğinden anonim mutate yoktur.
- Google denetimi için ürün sürümü, tool envanteri, consent metni, veri akışı, RMF matrisi ve tarihli test
  kanıtları saklanır; denetimi engelleyen veya API faaliyetini gizleyen mekanizma kurulmaz.
- Developer token 90 gün kesintisiz kullanılmazsa iptal edilebileceği için token durumu API Center'da izlenir;
  bunu önlemek amacıyla sahte trafik üretilmez.

### Uyum takip tablosu

| Kapı | Kanıt | Durum |
|---|---|---|
| Developer token | API Center ekranı/başvuru kaydı | Bekliyor |
| Permissible use | Google onay yazısı | Bekliyor |
| RMF sınıflandırması | Compliance ekibi yazılı cevabı | Bloklayıcı |
| OAuth scope sınıfı | Cloud Console Data Access | Bekliyor |
| Brand/scope verification | Verification Center onayı | Bloklayıcı |
| Restricted-scope security assessment | Google/empanelled assessor sonucu veya uygulanmaz teyidi | Bloklayıcı |
| Tool kullanım amacı | Token başvurusu + Tool Change Form geçmişi | Bloklayıcı |
| Compliance demo | 7 gün içinde sunulabilir reviewer hesabı/runbook | Bekliyor |
| Homepage/privacy/terms/support | Public HTTPS URL'ler | Bloklayıcı |
| Standard Access | API Center onayı | GA hedefi |

## Açık sorular

- Google Compliance'ın special-purpose/full-service sınıflandırması ve uygulanabilir RMF listesi.
- İlk production erişim seviyesi ve beta kullanıcı/quota sınırı.
- Doğrulanmış domain, ürün adı, support email ve tüzel/gerçek kişi başvuru sahibi.
- Restricted-scope security assessment'ın veri mimarimize göre kesin seviyesi, assessor, yıllık maliyeti ve takvimi.

## Güncelleme geçmişi

- 2026-07-17 — Standard Access hedefi, RMF sınıflandırma kapısı ve OAuth verification paketi araştırıldı.
- 2026-07-17 — Restricted scope, security assessment, Tool Change Form, denetim/demo ve end-user abuse
  sorumlulukları resmî Google kaynaklarıyla eklendi.
