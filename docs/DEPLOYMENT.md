# Dağıtım ve altyapı tasarımı

**Durum:** Kabul edildi (sağlayıcı seçimi hariç)  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Kodun tekrarlanabilir, izlenebilir ve geri alınabilir biçimde build edilmesi; ortamların ayrılması,
secret'ların güvenli verilmesi ve production yazma yetkisinin kontrollü dağıtılması.

## Araştırma

- GitHub Actions [Secure use reference](https://docs.github.com/en/actions/reference/security/secure-use),
  third-party action'ları full-length commit SHA'ya pinlemenin immutable kullanım için tek yol olduğunu belirtir.
- GitHub [Deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments),
  environment secret'ları, required reviewer ve deployment protection kurallarını destekler.
- [SLSA v1.2](https://slsa.dev/spec/v1.2/) build provenance ile artifact'ın kaynak ve build sürecine kadar
  izlenmesini; hosted build ve imzalı provenance'ın yükseltilmiş güvence sağladığını tanımlar.
- Docker [build best practices](https://docs.docker.com/build/building/best-practices/), minimal güvenilir
  base, multi-stage build, digest pinleme, gereksiz paketleri kaldırma ve non-root `USER` önerir.
- [OWASP DevSecOps](https://owasp.org/www-project-devsecops-guideline/), pipeline'da secret scan, SAST ve
  software composition analysis gibi erken güvenlik kontrollerini önerir.

## Karar

- Kaynak ve workflow yalnız `docs/REPOSITORY.md` içindeki GitHub reposundadır. CI GitHub Actions olur;
  action referansları full commit SHA'ya pinlenir, workflow izinleri varsayılan read-only ve job bazında minimumdur.
- PR kapısı: format/lint, type check, unit/integration/contract, principal izolasyon testleri, secret scan, SAST,
  dependency/SCA ve container scan. Production deployment yalnız korumalı `main` commit'inden olur.
- Tek immutable OCI image multi-stage build ile üretilir; minimal official/verified base digest'e pinlenir,
  non-root/read-only filesystem, ayrı writable temp, health endpoints ve graceful shutdown kullanır.
  Runtime health probes: `/healthz` liveness için, `/readyz` DB readiness için kullanılır; readiness `503`
  dönerse instance trafik almamalıdır.
- Image commit SHA + semantic release ile etiketlenir; SBOM ve build provenance oluşturulur, digest ile deploy
  edilir. Hedef SLSA Build L2 seviyesine yaklaşmaktır; exact compliance ayrıca doğrulanır.
- `local`, `staging`, `production` farklı cloud project/account, DB, OAuth client, Ads credential, Anthropic key,
  KMS ve network kullanır. Production verisi alt ortama kopyalanmaz.
- CI'da uzun ömürlü cloud key tutulmaz; seçilen sağlayıcı destekliyorsa GitHub OIDC ile kısa ömürlü federated
  credential alınır. Runtime secret'ı image/env dosyasına gömülmez, workload identity ile secrets manager'dan alınır.
- Production environment required reviewer ister; deploy'u başlatan kişinin kendi deploy'unu onaylaması
  engellenir. DB migration ayrı kontrollü job'dur; önce backup/compatibility, sonra app rollout.
- Rolling/canary deployment ve otomatik health rollback desteklenir. Write path ayrı kill switch'tir; rollback
  DB'de destructive down migration çalıştırmaz, ileri uyumlu app/image geri dönüşü kullanır.
- Yalnız MCP/OAuth/health/public legal-doc ingress uçları TLS ile açıktır; DB/queue/secrets public değildir.
  Egress Google, gerekli Anthropic bağlantıları, telemetry ve secrets uçlarıyla sınırlandırılır. Anthropic'in
  yayınladığı egress aralığı/WAF uyumu test edilir; admin erişimi kimlikli ve auditlidir. Anthropic'in MCP/OAuth
  discovery isteklerinin geldiği güncel [yayınlanmış IP aralığı](https://platform.claude.com/docs/en/api/ip-addresses)
  `160.79.104.0/21`'dir (2026-07-22'de doğrulandı) — ingress WAF/allowlist kuralları bu aralığı reddetmemelidir;
  aksi halde connector auth server'a hiç istek ulaşmaz (bkz. `docs/CONNECTOR_SUBMISSION.md` Faz 12.1 denetimi).
- Kesin cloud/region/topoloji, maliyet ve veri yerleşimi değerlendirmesiyle ADR'de seçilecektir; sağlayıcı
  seçilmeden production altyapı kodu yazılmaz.

## Açık sorular

- Cloud sağlayıcı, region ve yönetilen container/DB/queue/secrets ürünleri.
- Infrastructure-as-code aracı (aday: Terraform/OpenTofu) ve state backend'i.
- Availability hedefi, min/max instance, RPO/RTO ve disaster recovery region'u.
- Container registry signing/verification aracı ve SLSA hedef seviyesinin doğrulanması.

## Faz 10 uygulama durumu

- `backend/uv.lock`, uv 0.11.29 ve Python 3.11 ile üretildi; local/CI `uv sync --frozen` kullanır.
- `.github/workflows/ci.yml` ayrı lint/format, type, Python 3.11/3.13 test, docs, security ve migration
  job'larıdır. İzinler minimum, action referansları tam SHA; PR/fork job'larına production secret verilmez.
- `Dockerfile`, digest-pinned Python 3.11.13 slim multi-stage build, UID/GID 10001, healthcheck ve frozen
  production sync kullanır. `.dockerignore` secret/test/VCS bağlamını dışlar.
- `supply-chain.yml` commit-SHA image için SPDX SBOM ve high/critical vulnerability kapısı kurar.
- `deploy.yml` yalnız manual immutable digest kabul eder; main/docs preflight ve staging/production environment
  kapıları vardır. Provider adapter olmadığı için deploy/apply yapmaz. Rollback down migration değil önceki
  uyumlu image digest'ine trafik dönüşüdür; destructive migration otomatik rollback'i engeller.
- Docker CLI bu makinede yoktur; gerçek build/scan kanıtı GitHub Actions container job'ına aittir.

## Güncelleme geçmişi

- 2026-07-22 — Faz 12.1: Anthropic'in güncel egress IP aralığı (`160.79.104.0/21`) kaynak linkiyle
  belgelendi (önceden yalnız genel "yayınlanan aralık" ifadesi vardı).
- 2026-07-22 — Frozen uv lock, ayrı CI job'ları, digest-pinned non-root container, SBOM/scan ve
  provider-gated manual deployment workflow'u eklendi.
- 2026-07-17 — GitHub Actions, immutable OCI, OIDC secrets, environment approval ve sağlayıcı-bağımsız topoloji seçildi.
- 2026-07-17 — `/healthz` ve `/readyz` probe sözleşmesi deployment kararına eklendi.
