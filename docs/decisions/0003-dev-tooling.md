# ADR-0003: Geliştirme kalite araçları (format/lint, type check, test runner, secret/SAST/dependency tarama, lockfile)

- Durum: Kabul edildi
- Tarih: 2026-07-18
- Sahip: Ürün sahibi onayıyla ajan (Claude Code)

## Bağlam

`docs/TESTING.md` → "Açık sorular" ve `todo.md` madde 1.4, ilk iskeletin bilinçli olarak
bağımlılıksız (`unittest`) kaldığını, ancak Python formatter/linter/type checker/test runner/
coverage eşiği, secret scanner, SAST ve dependency scanner seçiminin uygulama bağımlılıklarına
geçilirken karara bağlanacağını belirtiyor. `backend/pyproject.toml` artık gerçek production
bağımlılıkları (FastAPI, google-ads, mcp, cryptography) taşıyor; bu ADR, bu artıştan sonraki adım
olarak geliştirme/CI kalite aracı setini kapatır. `AGENTS.md`in "minimum araç seti" beklentisi ve
`todo.md` 10.1/10.2'nin ileride kilitli lockfile ve ayrı CI job'ları gerektirmesi göz önünde
tutuldu. Sürüm numaraları 2026-07-18'de doğrudan PyPI JSON API'sinden (`pypi.org/pypi/<paket>/json`)
doğrulandı.

## Seçenekler

**Format/lint:** Ruff (tek Rust ikili, formatter+linter, Black-uyumlu çıktı, flake8/isort/pyupgrade
yerine geçer) vs ayrı Black+Flake8+isort — Ruff onlarca kat daha hızlı ve FastAPI/Pydantic gibi
projelerin de facto standardı; tek araçla format+lint kapsanır.

**Type checker:** Pyright (yüksek typing-spec uyumu, Pydantic v2 native desteği, hızlı) vs mypy
(daha büyük plugin ekosistemi — Django-stubs, SQLAlchemy-stubs). Bu proje henüz mypy plugin'lerine
bağımlı bir ORM/plugin deseni kullanmıyor (SQLAlchemy entegrasyonu ADR-0001'e göre henüz
kurulmadı); yeni proje için Pyright tercih edildi.

**Test runner:** pytest (mevcut `unittest.TestCase` tabanlı tüm testleri değişiklik gerektirmeden
keşfedip çalıştırır, `pytest-cov` ile coverage eklenir) vs sade `unittest` runner — pytest daha iyi
hata çıktısı, `-k`/`-x` gibi seçici çalıştırma ve coverage entegrasyonu sağlıyor; mevcut testlerin
yeniden yazılmasını gerektirmiyor.

**Secret scanner:** detect-secrets (saf Python, pip ile kurulur, Windows'ta ek ikili yönetimi
gerektirmez, `--baseline` iş akışıyla mevcut fixture'lardaki kasıtlı sahte token benzeri değerleri
tek seferde onaylayıp yeni sızıntıları yakalar) vs gitleaks (ayrı Go ikili kurulumu gerektirir,
proje pip-tabanlı araç zincirinden çıkar). Bu repo henüz büyük bir legacy taban değil ama testlerde
gerçekçi görünümlü sahte secret fixture'ları zaten var (`AGENTS.md` "gerçek secret asla" kuralı);
baseline yaklaşımı bunları tek seferde ayırt eder.

**SAST:** Bandit (Python-özel, tek amaçlı, pip ile kurulur, konfigürasyonu küçük) vs Semgrep
(çoklu dil, YAML kural setleri, kayıt/registry erişimi gerektirebilir) — proje tek dilli (Python)
ve küçük olduğu için Semgrep'in ek karmaşıklığı gerekçesiz; Bandit minimum araç seti ilkesine uyuyor.

**Dependency scanner:** pip-audit (PyPA + Trail of Bits/Google destekli resmi araç, OSV/PyPI
Advisory/GHSA kaynaklarını birleştirir, `pyproject.toml`'u doğrudan okur) — tek gerçekçi aday;
alternatif olan `safety` ticari veritabanına bağımlı.

**Lockfile stratejisi:** uv (`uv.lock` ile platformdan bağımsız, hash'li, `--frozen` ile sapmayı
reddeden kilit dosyası) vs pip-tools (`pip-compile --generate-hashes` opsiyonel, platforma özel) —
uv yeni projeler için önerilen varsayılan ve hash kilitleme varsayılan davranış; ancak gerçek
`uv.lock` üretimi ve README kurulum akışının uv'ye taşınması `todo.md` 10.1'in kapsamı (bu ADR
yalnız "hangi araç" sorusunu kapatır, ADR-0001'in HTTP/DB/OAuth kütüphaneleri için yaptığı gibi).

## Karar

- **Format/lint:** Ruff `>=0.15.22` (`ruff format` + `ruff check`).
- **Type checker:** Pyright `>=1.1.411`, `basic` type-checking modu (ilk artışta `strict` zorunlu
  değil; mevcut kodun kademeli tip anotasyonu tamamlanınca sıkılaştırılır).
- **Test runner:** pytest `>=9.1.1` + `pytest-cov>=7.1.0`; mevcut `unittest.TestCase` testleri
  değiştirilmeden `pytest backend/tests` ile çalıştırılabilir (pytest unittest keşfini destekler).
  Coverage eşiği: proje geneli **%80** (satır bazlı), tek dosya eşiği yok — `docs/TESTING.md`
  "Pytest coverage threshold" araştırmasına göre başlangıç eşiği gerçek kapsamın biraz altında
  tutulup zamanla yükseltilir; %100 hedeflenmez (son yüzdeler genelde savunma amaçlı erişilemeyen
  kod olur).
- **Secret scanner:** detect-secrets `>=1.5.0`, `.secrets.baseline` dosyasıyla.
- **SAST:** Bandit `>=1.9.4`.
- **Dependency scanner:** pip-audit `>=2.10.1`.
- **Lockfile stratejisi:** uv seçildi; gerçek `uv.lock` üretimi ve geliştirici kurulum akışının
  uv'ye taşınması `todo.md` 10.1'e ertelendi. Bu ADR yalnız aracı seçer.
- **Kurulum:** Bu araçlar `backend/pyproject.toml` → `[project.optional-dependencies].dev` grubuna
  eklendi (`pip install -e "backend[dev]"`). Production bağımlılıkları değişmedi.
- **Komutlar:** `README.md` ve `docs/TESTING.md`, yerel ve gelecekteki CI (`todo.md` 10.2) için
  aynı komutları kullanır: `ruff format --check backend`, `ruff check backend`, `pyright backend/src`,
  `pytest backend/tests --cov=src --cov-fail-under=80`,
  `bandit -c backend/pyproject.toml -r backend/src`,
  `detect-secrets-hook --baseline .secrets.baseline <dosyalar>` (denetim; baseline'ı değiştirmez --
  baseline'ı yeniden üretmek için `detect-secrets scan --all-files > .secrets.baseline` kullanılır,
  Windows'ta ardından yol ayırıcı normalizasyonu gerekir), `pip-audit`.

## Sonuçlar

- `docs/TESTING.md` "Açık sorular" bölümündeki formatter/linter/type checker/test runner/coverage
  eşiği sorusu bu ADR ile kapatıldı; ilgili bölüm güncellendi.
- Mevcut 295 testin hiçbiri yeniden yazılmadı; pytest bunları olduğu gibi çalıştırır (coverage
  %90.96, %80 tabanının üzerinde).
- Her araç bu değişiklikte gerçekten kuruldu ve repo üzerinde çalıştırıldı (bkz.
  `docs/TESTING.md` "Kalite kapısı" -- ayrıntılı bulgu listesi). Özet: Ruff+Pyright iki gerçek hata
  buldu (düzeltildi) ve 6 kullanılmayan import (kaldırıldı); Bandit'in 12 bulgusunun tamamı
  satır bazlı gerekçeli `# nosec` ile kapatıldı (gerçek bir güvenlik açığı değil); detect-secrets
  mevcut sahte test/doc fixture'larını `.secrets.baseline`'a kaydetti ve gerçek bir yeni secret'i
  doğrulanabilir şekilde reddettiği kanıtlandı (Windows'ta baseline yeniden üretimi yol ayırıcı
  normalizasyonu gerektirir -- bilinen platform kısıtı, `docs/TESTING.md`de belgelendi). pip-audit
  bu oturumun sandbox Python kurulumunda (`venv` stdlib modülü eksik) çalıştırılamadı; bu bir
  ortam kısıtıdır, `todo.md` 10.2 CI pipeline'ında veya standart bir yerel kurulumda doğrulanmalıdır.
- Mevcut kodun `ruff format`/`ruff check --fix` (~370 mekanik bulgu) ve `pyright` (56 tip hatası,
  çoğunlukla eksik/gevşek anotasyon) borcu bilinçli olarak bu değişiklikte kapatılmadı -- bu
  güvenlik hardening branch'ini ilgisiz bir reformat/anotasyon diff'iyle şişirmemek içindir; ayrı,
  yalnız-format ve yalnız-anotasyon commit'leri `todo.md` 1.6/1.7 olarak eklendi.
- Geri alma: bu seçimler `todo.md` 10.2 (CI pipeline) kurulurken veya trafik/ekip büyüklüğü
  değiştiğinde yeniden değerlendirilebilir; değişiklik yeni bir ADR gerektirir.

## Kaynaklar

- [Ruff — Astral Docs](https://docs.astral.sh/ruff/)
- [Ruff FAQ — Black/Flake8/isort uyumluluğu](https://docs.astral.sh/ruff/faq/)
- [Pyright — mypy/Pyright/ty karşılaştırması (2026)](https://www.danilchenko.dev/posts/ty-vs-mypy-vs-pyright/)
- [pip-audit — PyPA GitHub](https://github.com/pypa/pip-audit)
- [pip-audit — PyPI](https://pypi.org/project/pip-audit/)
- [detect-secrets — baseline iş akışı](https://github.com/Yelp/detect-secrets)
- [Gitleaks — GitHub](https://github.com/gitleaks/gitleaks)
- [Bandit vs Semgrep (2026) karşılaştırması](https://appsecsanta.com/sast-tools/bandit-vs-semgrep)
- [uv — Locking environments, Astral Docs](https://docs.astral.sh/uv/pip/compile/)
- PyPI JSON API (`https://pypi.org/pypi/<paket>/json`) — `ruff` 0.15.22, `pyright` 1.1.411,
  `pytest` 9.1.1, `pytest-cov` 7.1.0, `bandit` 1.9.4, `detect-secrets` 1.5.0, `pip-audit` 2.10.1
  (doğrulama tarihi: 2026-07-18).
- `docs/TESTING.md`, `docs/decisions/0001-backend-stack.md`, `AGENTS.md` (iç kaynaklar).
