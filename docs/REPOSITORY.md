# Repository ve Git çalışma düzeni

**Durum:** Kabul edildi  
**Son gözden geçirme:** 2026-07-17  
**Sonraki gözden geçirme:** 2026-10-17

## Amaç

Projenin tek yetkili uzak repository adresini, fetch/pull/push hedefini ve güvenli Git çalışma
kurallarını belirlemek.

## Araştırma

- GitHub, mevcut yerel kodu bağlamak için remote adının `origin` olmasını, URL'nin `git remote add
  origin REMOTE-URL` ile eklenmesini ve `git remote -v` ile doğrulanmasını önerir:
  [Adding locally hosted code to GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github).
- Var olan `origin` yanlışsa silip yeniden oluşturmak yerine `git remote set-url origin ...` ile
  güncellenebilir: [Managing remote repositories](https://docs.github.com/en/get-started/git-basics/managing-remote-repositories).
- GitHub, parola/API key gibi hassas bilgilerin hiçbir zaman add/commit/push edilmemesini açıkça
  şart koşar. Push protection desteklenen secret'ları remote'a ulaşmadan engelleyebilir:
  [Push protection](https://docs.github.com/en/code-security/concepts/secret-security/push-protection).
- Ana branch için PR incelemesi, başarılı status check, force-push/silme engeli gibi kurallar branch
  protection ile uygulanabilir:
  [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

## Karar

- **Yetkili GitHub sayfası:** https://github.com/kocennes/AddObserver
- **Canonical HTTPS remote:** `https://github.com/kocennes/AddObserver.git`
- **Remote adı:** `origin`
- **Ana branch:** `main`
- Bu proje için clone, fetch, pull ve push işlemleri yalnız yukarıdaki repository üzerinden yapılır.
- İşleme başlamadan önce mevcut remote `git remote -v` ile doğrulanır. `origin` yoksa eklenir; başka
  URL'ye bakıyorsa kullanıcıya bilgi verilerek `git remote set-url` kullanılır.
- Uzak değişiklikler önce `git fetch origin` ile incelenir. Kullanıcının yerel değişiklikleri varken
  otomatik merge/rebase veya zorlayıcı işlem yapılmaz.
- Doğrudan `main` üzerine force-push yapılmaz. Özellik çalışmaları kısa ömürlü branch + PR şeklindedir.
- Push öncesi status/diff, test kalite kapıları ve secret taraması kontrol edilir. `.env`, token,
  credential veya müşteri verisi hiçbir koşulda push edilmez.
- Güvenlik değişikliklerinin commit mesajında mevcut proje kuralına göre `[security]` etiketi bulunur.
- Ajan, kullanıcı açıkça istemeden commit veya push yapmaz.

İlk bağlantı için, klasör Git deposu değilse uygulanacak komut dizisi:

```powershell
git init -b main
git remote add origin https://github.com/kocennes/AddObserver.git
git remote -v
```

Klasör zaten Git deposuysa yalnız remote doğrulanır; yeniden `git init` çalıştırılmaz.

## Açık sorular

- Repository görünürlüğü ve ekip erişim rolleri GitHub üzerinden doğrulanacak.
- `main` branch protection altında zorunlu review ve status check sayısı belirlenecek.
- Merge yöntemi (squash/rebase/merge commit) ekip tarafından seçilecek.
- GitHub Actions ve push protection kullanılabilirliği repository planına göre doğrulanacak.

## Güncelleme geçmişi

- 2026-07-17 — Canonical GitHub repository, `origin`, `main` ve güvenli push/pull kuralları tanımlandı.

