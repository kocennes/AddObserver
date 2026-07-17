# CLAUDE.md

Bu proje için birincil kaynak `AGENTS.md` dosyasıdır — kod yazmaya başlamadan önce onu oku.
Tüm proje bağlamı, klasör yapısı, güvenlik kuralları, kod stili ve test kuralları orada.

## Claude Code'a özel notlar
- Güvenlik araştırması yaparken web search kullan; 2026 itibarıyla güncel kaynaklara bak,
  eski/genel bilgiyle yetinme.
- `docs/SECURITY.md` boş veya eksikse, herhangi bir backend kodu yazmadan önce onu
  tamamla — bu adımı atlama (bkz. `AGENTS.md` → "Güvenlik" bölümü).
- Bu backend'in kendisi ileride bir MCP sunucusu olarak Claude'a bağlanacak
  (`backend/src/mcp/`). Bu bölümü yazarken resmi MCP dokümantasyonuna bak:
  https://modelcontextprotocol.io
- Uzun/karmaşık mimari kararlar öncesi kısa bir plan yaz, onayımı bekle, sonra uygula.
- Google Ads API ile ilgili belirsizliklerde tahmin etme — resmi dokümantasyonu
  (developers.google.com/google-ads/api) kontrol et.
