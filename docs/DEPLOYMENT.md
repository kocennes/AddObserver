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
  yayınladığı egress aralığı/WAF uyumu test edilir; admin erişimi kimlikli ve auditlidir.
- Kesin cloud/region/topoloji, maliyet ve veri yerleşimi değerlendirmesiyle ADR'de seçilecektir; sağlayıcı
  seçilmeden production altyapı kodu yazılmaz.

## Açık sorular

- Cloud sağlayıcı, region ve yönetilen container/DB/queue/secrets ürünleri.
- Infrastructure-as-code aracı (aday: Terraform/OpenTofu) ve state backend'i.
- Availability hedefi, min/max instance, RPO/RTO ve disaster recovery region'u.
- Container registry signing/verification aracı ve SLSA hedef seviyesinin doğrulanması.

## Güncelleme geçmişi

- 2026-07-17 — GitHub Actions, immutable OCI, OIDC secrets, environment approval ve sağlayıcı-bağımsız topoloji seçildi.
