# MCP sunucusu ve tool sözleşmeleri

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17

## Amaç

Public Streamable HTTP MCP server'ın tool, auth, veri minimizasyonu ve prompt injection sınırlarını belirlemek.

## Araştırma

- Anthropic [review criteria](https://claude.com/docs/connectors/building/review-criteria) read/write ayrımı,
  annotations, kısa ad, dar açıklama ve functional test gerektirir.
- [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
  confused deputy, passthrough, SSRF ve session saldırılarını tanımlar.

## Karar
**Sonraki gözden geçirme:** 2026-10-17

## Sınır

MCP, internete açık Streamable HTTP connector katmanıdır; Google Ads credential proxy'si değildir. Directory
teknik/inceleme gereksinimlerinin ayrıntısı `CONNECTOR_SUBMISSION.md` içindedir.

## Tool tasarım kuralları

- Bir tool tek sorumluluk taşır; adı eylemi açıklar (`get_campaign_metrics`, `prepare_proposal`).
- Input/output JSON Schema kapalıdır (`additionalProperties: false`), alan boyutları ve enum'lar sınırlıdır.
- Tool kimlik bağlamını argümandan almaz; audience-bound connector token subject'inden türetir.
- Salt okunur veri çekme ile proposal hazırlama ve uygulama ayrı tool'lardır.
- Model için doğrudan “raw mutate” tool'u sunulmaz.
- Uygulama tool'u proposal ID dışında değiştirilebilir payload almaz ve backend insan onayını doğrular.
- Her tool timeout, cancellation, maksimum sonuç boyutu, rate limit ve güvenli hata koduna sahiptir.
- Sonuçlar minimum gerekli alanları döndürür; token, prompt içi gizli talimat ve başka kullanıcı verisi yoktur.
- Her tool ≤64 karakter ad, `title` ve uygulanabilir `readOnlyHint`/`destructiveHint` taşır. Read/write aynı
  tool veya method parametreli catch-all tool içinde birleşmez.

## Prompt injection sınırı

- Google Ads metinleri, URL'ler ve model çıktıları `untrusted_data` kabul edilir.
- Bu içerikteki “talimatlar” system policy, principal, scope veya onay durumunu değiştiremez.
- Veriden tool adı/argümanı kopyalanıp yürütülmez; schema + policy + ownership doğrulaması yapılır.
- Tool açıklamaları yetki iddia etmez ve modele secret istemesini söylemez.

## Yetkilendirme

Remote MCP zorunludur; güncel [MCP Authorization 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization),
[Anthropic connector authentication](https://claude.com/docs/connectors/building/authentication) ve
[Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
izlenir: Streamable HTTP, HTTPS, protected-resource/AS discovery, PKCE S256, exact redirect URI, audience,
token lifecycle ve Origin validation. Upstream Google token passthrough yasaktır.

## Tool ekleme kontrolü

- Tehdit modeli ve veri sınıflandırması yapıldı mı?
- En dar scope ve principal→Google account access doğrulandı mı?
- Pozitif, schema negatif, yetkisiz, cross-user, injection ve timeout testleri var mı?
- Ad/title/annotation ve MCP Inspector + Claude custom connector testleri tamam mı?
- Yazma ise immutable onay ve audit kapısından geçiyor mu?
- `API_CONTRACTS.md` ve `SECURITY.md` ile tutarlı mı?

## Açık sorular

- Google Ads'e gerçekten mutate çağrısı yapan execution tool'unun directory v1'e dahil olup olmayacağı
  (`prepare_proposal` bu sorunun dışındadır — yalnız kendi DB'mize yazar, Google Ads'e dokunmaz).
- MCP SDK/transport sürümü ve MCP Apps ihtiyacı.

## İlk tool envanteri (uygulandı)

| Tool | Annotations | Google Ads'e yazar mı |
|---|---|---|
| `list_accessible_accounts` | read-only, local | Hayır |
| `get_campaign_performance` / `get_ad_group_performance` / `get_keyword_performance` | read-only | Hayır (okur) |
| `prepare_proposal` | write, local, non-destructive | Hayır — yalnız `proposal` tablosuna taslak yazar |
| `get_proposal` | read-only, local | Hayır |

`prepare_proposal`'ın kabul ettiği `proposal_type` docs/PRODUCT.md Faz 1.1 allowlist'iyle birebir sınırlıdır
(`campaign_pause`, `campaign_enable`, `campaign_budget_update` — bkz. `backend/src/approval/payload_schema.py`);
onay/uygulama tool'u henüz yoktur.

## Güncelleme geçmişi

- 2026-07-17 — Remote directory auth, annotations ve cross-user izolasyon kuralları eklendi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi; review checklist kanıtları
  submission öncesi `CONNECTOR_SUBMISSION.md` üzerinden ayrıca tamamlanacak.
- 2026-07-17 — `prepare_proposal`/`get_proposal` uygulandı; ilk tool envanteri tablosu eklendi.
