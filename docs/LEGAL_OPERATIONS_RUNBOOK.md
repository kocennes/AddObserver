# Hukuki operasyonlar ve veri ihlali karar runbook'u

**Durum:** Taslak — hukukçu süre/rol kararı olmadan production'da çalıştırılamaz  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Kullanıcı hakları (11.5 tasarımı)

1. Talep yalnız hukukçu onaylı privacy/support kanalından alınır; ticket'a rastgele Ads verisi eklenmez.
2. Kimlik, mevcut connector oturumunda step-up veya hesabın kayıtlı ve doğrulanmış kanalıyla doğrulanır.
   Kimlik belgesi ancak hukuk kararı zorunlu kılarsa, ayrı restricted store ve kısa TTL ile işlenir.
3. Talep `access/export/correction/deletion/objection/withdrawal` olarak sınıflanır; principal scope'u sabitlenir.
4. Legal hold ve uygulanabilir SLA hukuk matrisinden belirlenir. Süre henüz kararlaştırılmadığı için otomatik promise yoktur.
5. Export yalnız principal'a ait allowlist alanlarından, şifreli ve süreli teslimle üretilir; secret/token/audit
   bütünlük metadata'sı veya başka principal verisi çıkmaz.
6. Disconnect önce Google revoke + planlı işleri durdurma + vault delete yapar. Account deletion aktif DB/cache'i
   purge eder; dar legal hold ayrıştırılır; backup erişimi kapanır ve onaylı pencere sonunda purge edilir.
7. Her adım actor, principal, request type, zaman, doğrulama yöntemi, karar nedeni, etkilenen sistem ve outcome ile audit edilir.
8. Support normal rolde toplu principalsiz sorgu yapamaz. İstisna süreli break-glass, amaç, çift onay ve ayrı audit gerektirir.

Bu akışın endpoint/DB/worker implementasyonu; hukukçu SLA, retention, hold ve kimlik doğrulama kararından sonra,
`DATA_MODEL.md` güncellemesi ve izolasyon/backup-purge testleriyle yapılır.

## Veri ihlali (11.10 karar çerçevesi)

### Teknik akış

1. Tespit/triage: on-call olayı açar, correlation ve ilk gözlemi kaydeder; şüpheli write fail-closed kapatılır.
2. Containment: credential revoke/rotate, session family revoke, erişim daraltma; delil silinmez/değiştirilmez.
3. Delil koruma: UTC timeline, immutable snapshot/hash, erişim zinciri; token ve gereksiz PII olay kanalına kopyalanmaz.
4. Kapsam: etkilenen principal/customer, veri kategorisi, süre, alıcı, risk ve devam eden exposure belirlenir.
5. Hukuk escalation: hukuk sorumlusu hedef ülke ve rol matrisinden regulator/ilgili kişi yükümlülüğünü belirler.
6. Vendor escalation: sözleşme/politika tetiklenirse Google, Anthropic ve subprocessors için yetkili owner devreye girer.
7. Mesaj: yalnız doğrulanmış olgular, etki, alınan önlem, kullanıcı adımı ve contact; hukuk + incident commander onayı.
8. Recovery/postmortem: kontrollü restore, izleme, root cause, düzeltici faaliyet ve bildirim kanıtı retention'a alınır.

### Hukukçuya karar tablosu

| Konu | Karar bekleniyor |
|---|---|
| Incident commander, privacy/legal owner, regulator owner, Google/Anthropic owner | İsim/rol ve yedek kişi |
| Hedef ülke ve controller/processor rolüne göre bildirim eşiği | Risk/olay matrisi |
| Kurum, ilgili kişi, Google, Anthropic ve processor bildirim süresi | Her biri için tetikleyici ve başlangıç anı; varsayımsal süre yazılmaz |
| Mesaj onay ve çok dilli iletişim | Approver + güvenli kanal |
| Incident/delil/bildirim kaydı retention ve legal hold | Süre + store + erişim |

### Masaüstü tatbikat senaryosu (onay sonrası)

Bir log provider alarmı, iki principal'ın `customer_id` sonuçlarının karışmış olabileceğini bildirir. Katılımcılar
write kill-switch, token isolation, immutable delil, kapsam sorgusu, hukuk escalation, vendor/user mesaj taslağı,
recovery ve postmortem adımlarını yürütür. Başarı: başka principal verisi support export'una çıkmaz; süreler hukuk
matrisinden hesaplanır; tüm karar/audit kanıtı vardır. Tatbikat tarihi/katılımcı/sonuç hukuk onayından sonra eklenir.

## Kaynaklar

- [KVKK kişisel veri ihlali bildirim formu](https://www.kvkk.gov.tr/SharedFolderServer/CMSFiles/ba4d9b1e-8669-4cbd-9af2-882c4fc8a40e.pdf)
- [GDPR, özellikle Madde 33–34](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [Google OAuth security assessment](https://support.google.com/cloud/answer/13465431)

## Değişiklik geçmişi

- 2026-07-22 — Faz 11.5 ve 11.10 için fail-closed hak talebi, breach karar akışı ve tatbikat senaryosu hazırlandı.
