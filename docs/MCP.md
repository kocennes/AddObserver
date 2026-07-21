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

- MCP SDK/transport sürümü ve MCP Apps ihtiyacı.

## İlk tool envanteri (uygulandı)

| Tool | Annotations | Google Ads'e yazar mı |
|---|---|---|
| `list_accessible_accounts` | read-only, local | Hayır |
| `get_campaign_performance` / `get_ad_group_performance` / `get_keyword_performance` | read-only | Hayır (okur) |
| `prepare_proposal` | write, local, non-destructive | Hayır — yalnız `proposal` tablosuna taslak yazar |
| `get_proposal` / `list_proposals` | read-only, local | Hayır |

`prepare_proposal`'ın kabul ettiği `proposal_type` docs/PRODUCT.md Faz 1.1 allowlist'iyle birebir sınırlıdır
(`campaign_pause`, `campaign_enable`, `campaign_budget_update` — bkz. `backend/src/approval/payload_schema.py`);
onay/uygulama tool'u Directory v1/Faz 1'e dahil değildir ve Faz 8 Google Compliance kapısı açılmadan eklenmez.

`get_proposal`, süresi dolmuş ama henüz kalıcı durum geçişi yazılmamış `pending_approval` önerilerini
cevapta `expired` olarak gösterir. `list_proposals`, yalnız çağıranın `principal_id` kapsamındaki, süresi
dolmamış `pending_approval` durumundaki önerileri döndürür. Opsiyonel `customer_id` filtresi verilirse
hesap bağlantısı tekrar doğrulanır; `limit` 1-100 arasında sınırlıdır ve varsayılan 50'dir.

Yedi Directory v1 tool'unun tamamı açık, kapalı output schema yayımlar. Reporting satırları yalnız ilgili
campaign/ad group/keyword alanlarını; proposal çıktıları yalnız sürümlü allowlist payload ve dış proposal
metadata'sını taşır. Input ve iç içe tüm output object şemaları `additionalProperties: false` kullanır.

## Güncelleme geçmişi

- 2026-07-22 — Üç reporting tool output'u bounded `rows`, encrypted `next_page_token`, `truncated`,
  `returned_row_count`, `response_bytes` ve `quota.google_requests` alanlarıyla genişletildi; provider
  token public MCP çıktısından kaldırıldı.

- 2026-07-19 — Tool envanteri contract testi ad/title/description, 64 karakter sınırı, principal argümanı
  yokluğu, read/write annotation ayrımı ve iç içe kapalı input/output schema sözleşmesini gerçek MCP
  `tools/list` cevabında doğrular. Yedi tool structured output ve explicit allowlist schema yayımlar.

- 2026-07-19 — MCP bearer doğrulaması production PostgreSQL exact-token bootstrap transaction'ına bağlandı;
  transaction tool yürütülmeden önce kapanır ve doğrulanmış principal ASGI request state üzerinden aktarılır.
- 2026-07-19 — `list_accessible_accounts` ve local proposal tool'ları principal-bound kısa PostgreSQL
  unit-of-work kullanır; Google Ads reporting ağ çağrıları açık DB transaction içine alınmaz.
- 2026-07-19 — Reporting account/credential metadata'sı kısa RLS transaction'ında çözülür; vault read ve
  Google Ads çağrısı transaction kapandıktan sonra yapılır, AUTH hata pasifleştirmesi ayrı transaction'dır.

- 2026-07-18 — Faz 1.1 kapsam kararı kapatıldı: Directory v1/Faz 1 tool envanteri read-only reporting,
  local `prepare_proposal` ve proposal read tool'larıyla sınırlıdır; Google Ads execution/apply tool'u
  Faz 8'e kadar yoktur.
- 2026-07-17 — Remote directory auth, annotations ve cross-user izolasyon kuralları eklendi.
- 2026-07-17 — Ürün sahibi onayıyla Kabul edildi durumuna geçirildi; review checklist kanıtları
  submission öncesi `CONNECTOR_SUBMISSION.md` üzerinden ayrıca tamamlanacak.
- 2026-07-17 — `list_proposals` bekleyen, principal-scoped öneri görünürlüğü için eklendi.
- 2026-07-17 — `prepare_proposal`/`get_proposal` uygulandı; ilk tool envanteri tablosu eklendi.
