# ADR-0008: Production hosting ve network sağlayıcısı

- Durum: Önerildi
- Tarih: 2026-07-22
- Sahip: Ürün sahibi kararı bekleniyor

## Bağlam

Public connector 7/24 HTTPS, managed PostgreSQL, KMS/secrets, WAF/rate limiting, telemetry, backup ve
üç ayrık ortam gerektirir. Ürün son kullanıcıya ücretsizdir; bu, altyapının ücretsiz olduğu anlamına gelmez.
Hedef ülke/veri yerleşimi, aylık bütçe, tüzel işletmeci ve RPO/RTO henüz belirlenmemiştir.

## Seçenekler

| Seçenek | Compute/DB/secrets/WAF | Güçlü taraf | Risk/maliyet |
|---|---|---|---|
| Google Cloud | Cloud Run, Cloud SQL PostgreSQL, Secret Manager/KMS, Load Balancer+Cloud Armor | Google Ads/OAuth operasyonuyla tek cloud; scale-to-zero ve resmi secure-serverless blueprint | Cloud SQL/VPC/LB sabit maliyeti; region/legal kararı gerekir |
| AWS | ECS Fargate, RDS PostgreSQL, Secrets Manager/KMS, ALB+WAF | Olgun IAM/network ve geniş region seçimi | Daha fazla bileşen/operasyon; task, ALB, NAT ve RDS sabit maliyeti |
| Azure | Container Apps, PostgreSQL Flexible Server, Key Vault, Front Door/WAF | Serverless container ve kurumsal identity | Ürün ekosistemiyle ek entegrasyon; region/fiyat doğrulaması gerekir |

Google Cloud Run kullanım bazlı ücretlenir ve free tier sunar; aynı-region Google Cloud transferleri için
belirtilen avantajlar vardır. Google'ın secure serverless blueprint'i Cloud Run + Load Balancer + Cloud Armor +
VPC + Secret Manager/KMS desenini doğrudan belgeler. Bu nedenle **teknik aday Google Cloud**'dur; ancak seçim
değildir.

## Öneri ve kabul kapısı

GCP `europe-west1` yalnız maliyet/servis bulunabilirliği için başlangıç adayıdır. Aşağıdakiler ürün sahibi ve
hukuk tarafından sağlanmadan ADR “Kabul edildi” yapılamaz:

1. İşletmeci ve hedef kullanıcı ülkeleri/veri yerleşimi kararı.
2. Aylık staging+production bütçe üst sınırı ve faturalama sahibi.
3. RPO/RTO, minimum instance/availability ve disaster-recovery beklentisi.
4. Google Cloud project/billing organization sahipliği ve yetkili deploy/on-call kişileri.

Bu kapı kapanana kadar vendor-specific IaC, managed KMS adapter veya production deploy yazılmaz/çalıştırılmaz.

## Kaynaklar

- https://cloud.google.com/run/pricing
- https://docs.cloud.google.com/architecture/blueprints/serverless-blueprint
- https://docs.cloud.google.com/run/docs/configuring/services/secrets
- https://aws.amazon.com/fargate/pricing/
- https://azure.microsoft.com/pricing/details/container-apps/
