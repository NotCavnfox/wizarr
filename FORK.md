# Fork notes

Homelab fork of `wizarrrr/wizarr`. Working branch: `main`.
Keep fork changes small; preserve them when accepting stable upstream releases.

## Source and releases

- Daily `fork-sync-upstream` merges a published stable release into the fork and
  opens a review PR. Merge history avoids replaying duplicate overlay commits.
  Retries reuse matching branches without force-pushes or repeated conflict comments.
- Independent review and passing CI precede source merge. Verify actual branch
  protection; this document does not enforce it. Source merges do not publish images.
- `fork-auto-release` is now a manual dispatcher despite its historical filename.
  After owner approval, supply exact `release_sha`, published stable `upstream_tag`,
  and `release_tag` (upstream tag or an unused `-fork.N` suffix). Latest exact-SHA fork
  CI must pass, source must be in `main` and contain upstream, and the selected
  publisher must match the current publisher. Existing tags are never moved.
- `fork-release` publishes only on explicit version-tag dispatch. Inherited upstream
  publishers are restricted to upstream; CI does not publish mutable edge/dev tags.
  Old tags still contain historical workflow code: never dispatch them casually.
- Image publication and production rollout are distinct actions. A server release
  needs the approved image/digest, data backup, verification and rollback plan.
  A database migration may prevent image-only rollback.

`GITHUB_TOKEN` cannot push changed workflow files. Such syncs need an authorized
agent/operator to publish the reviewed branch using an existing suitable credential;
do not add a broad personal token, hide failure or duplicate the scheduler.
If tag creation succeeds but dispatch fails, verify the immutable tag and manually
retry only its publisher after approval; do not move the tag or rerun blindly.

## Local checks

```bash
python3 test/test-fork-sync.py
python3 test/test-fork-release.py
docker build -t localbuild:ci .
PORT=5690 HEALTH_PATH=/ IMAGE=localbuild:ci bash test/smoke.sh
IMAGE=localbuild:ci docker compose -f test/compose.test.yml up -d --wait
```

Workflow regression tests need Git, Bash, jq and PyYAML; all network/publication
calls are mocked. Container tests are local integration checks, never production
maintenance commands. Track cross-repository work in NotCavnfox/homeserver#57.
