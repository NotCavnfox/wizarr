# Fork notes

This is a homelab fork of `https://github.com/wizarrrr/wizarr.git`, maintained to add features and accept
contributions. Image published to `ghcr.io/notcavnfox/wizarr`.

- **Working branch:** `main` (protected — PR + green `ci / fork-build-test` + 1 review).
- **Patch-overlay:** keep changes minimal; `fork-sync-upstream` rebases onto each
  upstream release nightly and opens a PR.
- **Tests:** `test/smoke.sh` (boot + HTTP 5690/) and `test/compose.test.yml`
  must stay green — that gate protects the live homelab.

## Local loop
```bash
docker build -t localbuild:ci .
PORT=5690 HEALTH_PATH=/ IMAGE=localbuild:ci bash test/smoke.sh
IMAGE=localbuild:ci docker compose -f test/compose.test.yml up -d --wait
```
