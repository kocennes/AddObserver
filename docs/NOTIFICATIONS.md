# Bekleyen onay bildirimi

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-22  
**Sonraki gözden geçirme:** 2026-10-22

## Amaç

`todo.md` Faz 7.5'in istediği gibi, Claude'un `prepare_proposal` ile oluşturduğu bekleyen bir
onayın kullanıcıya e-posta/Slack/webhook gibi ayrı bir kanaldan haber verilmesine gerçekten ihtiyaç
olup olmadığını karara bağlamak -- AGENTS.md "Yeni bir tasarım alanı ortaya çıkarsa" kuralı
gereği, bu belge kabul edilmeden hiçbir bildirim entegrasyonu yazılmaz.

## Araştırma

- `docs/PRODUCT.md` Faz 1 akışı: `prepare_proposal` yalnız Claude'un o anki, canlı bir konuşma
  içinde çağırdığı bir MCP tool'udur -- kullanıcı zaten Claude ile aktif olarak etkileşimdeyken
  öneri oluşur. Claude, tool sonucunu (`mcp/proposals.py`, `mcp/output_schemas.py`) doğrudan aynı
  konuşmada kullanıcıya döndürür ve `/approvals` bağlantısına yönlendirebilir; bu zaten
  eşzamanlı bir bildirim düzenidir.
- `docs/PRODUCT.md`'nin değişmez kabul kriterleri ve `docs/GOOGLE_API_ACCESS.md`: gerçek Google Ads
  mutate/execution (Faz 8) tamamen bloke. Bir öneri onaylanmadan hiçbir hesaba yazılmıyor VE
  onaylandıktan sonra da hiçbir otomatik uygulama yok (Faz 1/1.1 kapsamı yalnız yerel onay kaydı,
  bkz. `docs/decisions/`). Yani "onay saatlerce/günlerce beklenirse" bugün gerçekleşen tek sonuç,
  önerinin süresinin dolmasıdır (`approval/domain.py::submit_proposal`/`expires_at`) -- kaçırılan bir
  fırsat maliyeti var, ama canlı bir hesaba giden kaçırılmış bir mutate riski yok. Bu,
  "bildirimi kaçırmanın" güvenlik/iş etkisini bugün düşük tutuyor.
- Anthropic'in [Connectors overview](https://claude.com/docs/connectors/overview)'ı, remote MCP
  connector'ların yalnız Claude'un kendi yüzeylerinde (web/desktop/mobile/Code) kullanıldığını
  belirtir; connector'ın kendi başına push bildirimi göndermesi (email/Slack/webhook) beklenen bir
  connector özelliği değil, bu üründe henüz kurulmamış ayrı bir teslim kanalı gerektirir.
- Yeni bir kanal (e-posta/Slack/webhook) `docs/LEGAL.md`/`PRIVACY_POLICY.md` kapsamında yeni bir PII
  işleme yüzeyi (e-posta adresi/Slack workspace/webhook URL saklama), yeni bir consent/unsubscribe
  akışı, yeni bir rate-limit/abuse yüzeyi ve yeni bir teslim-audit gereksinimi ekler -- `docs/LEGAL.md`
  hâlâ `Taslak` olduğu için (hukukçu incelemesi bekliyor) bu kapsam bugün açılamaz bile.

## Karar

**Bugün doğrulanmış bir ihtiyaç yok; ayrı bir bildirim kanalı (e-posta/Slack/webhook) Faz 7.5
kapsamında eklenmez.** Gerekçe: (1) `prepare_proposal` zaten Claude'un o anki konuşmasında,
kullanıcı ile eşzamanlı olarak gerçekleşiyor -- kullanıcı zaten oradadır, ayrı bir "haber verme"
adımına ihtiyaç yok; (2) execution/write Faz 8'e kadar tamamen bloke olduğu için bir onayın
gecikmesinin tek sonucu süre dolumu, canlı hesaba giden kaçırılmış bir değişiklik değil; (3) yeni
bir kanal, `docs/LEGAL.md` kabul edilmeden açılamayacak yeni bir PII/consent/rate-limit/unsubscribe
yüzeyi ekler; (4) ücretsiz ürün kuralı (`AGENTS.md`) gereği her yeni operasyonel yüzey (ör. e-posta
gönderim altyapısı, Slack app'i) ek bakım/maliyet getirir ve bugünkü kapsam bunu gerektirmiyor.

Kod değişikliği yoktur; hiçbir email/Slack/webhook entegrasyonu eklenmedi.

## Açık sorular

- Yeniden değerlendirme tetikleyicileri: (a) `todo.md` 1.1/Faz 8 write/execution kapsamı açılırsa
  (o zaman "onay gecikmesi = kaçırılmış gerçek bir işlem penceresi" haline gelir, bkz.
  `docs/PRODUCT.md` Faz 1.1); (b) kullanıcı geri bildirimi bekleyen önerilerin fark edilmeden süresi
  dolduğunu gösterirse; (c) `docs/LEGAL.md` `Kabul edildi` olur ve yeni bir PII kanalı açmanın
  hukuki maliyeti netleşirse. Bu tetikleyicilerden biri gerçekleşmeden bu belge yeniden açılmaz.

## Güncelleme geçmişi

- 2026-07-22 — İlk oluşturma: Faz 7.5 kapsamında bildirim ihtiyacı değerlendirildi ve "bugün
  gerekli değil" kararıyla kapatıldı; hiçbir entegrasyon kodu eklenmedi.
