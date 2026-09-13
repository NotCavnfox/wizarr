#!/usr/bin/env python3
"""Exercise the release gate's actual shell with local Git and mocked GitHub."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / ".github/workflows/fork-auto-release.yml").read_text())
STEPS = [s["run"] for s in CONFIG["jobs"]["tag-and-release"]["steps"] if "run" in s]
BASE = CONFIG["env"]["BASE_BRANCH"]
UPSTREAM = CONFIG["env"]["UPSTREAM_REPO"]
REAL_GIT = shutil.which("git")
REAL_BASH = shutil.which("bash")


class ReleaseGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls.jsonl"
        self.git_calls = self.root / "git-calls.jsonl"
        self.state = self.root / "state.json"
        self.state.write_text(
            json.dumps(
                {
                    "release": {
                        "tag_name": "v1.2.3",
                        "draft": False,
                        "prerelease": False,
                        "published_at": "2026-09-13",
                    },
                    "runs": [{"status": "completed", "conclusion": "success"}],
                }
            )
        )
        (self.bin / "gh").write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
s = json.loads(Path(os.environ['FAKE_STATE']).read_text())
with open(os.environ['FAKE_CALLS'], 'a') as f: f.write(json.dumps(a)+'\\n')
if a[0] == 'api': print(json.dumps(s['release']))
elif a[:2] == ['run', 'list']: print(json.dumps(s['runs']))
elif a[:2] != ['workflow', 'run']: raise SystemExit('unexpected gh call')
""")
        (self.bin / "git").write_text("""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ['FAKE_GIT_CALLS'], 'a') as f: f.write(json.dumps(a)+'\\n')
a = [os.environ['LOCAL_UPSTREAM'] if x == 'https://github.com/'+os.environ['UPSTREAM_REPO']+'.git' else x for x in a]
os.execv(os.environ['REAL_GIT'], [os.environ['REAL_GIT']] + a)
""")
        for p in self.bin.iterdir():
            p.chmod(0o755)
        self.env = dict(
            os.environ,
            PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
            FAKE_STATE=str(self.state),
            FAKE_CALLS=str(self.calls),
            FAKE_GIT_CALLS=str(self.git_calls),
            REAL_GIT=REAL_GIT,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
            GITHUB_REPOSITORY="NotCavnfox/" + UPSTREAM.rsplit("/", 1)[1],
            UPSTREAM_REPO=UPSTREAM,
            BASE_BRANCH=BASE,
            UPSTREAM_TAG="v1.2.3",
            RELEASE_TAG="v1.2.3-fork.1",
            GITHUB_OUTPUT=str(self.root / "output"),
        )
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.git(self.seed, "init", "-b", BASE)
        p = self.seed / ".github/workflows/fork-release.yml"
        p.parent.mkdir(parents=True)
        p.write_text((ROOT / ".github/workflows/fork-release.yml").read_text())
        (self.seed / "app").write_text("base\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "upstream release")
        self.upstream_sha = self.git(self.seed, "rev-parse", "HEAD")
        self.git(self.seed, "tag", "v1.2.3")
        self.upstream = self.root / "upstream.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.upstream))
        self.env["LOCAL_UPSTREAM"] = str(self.upstream)
        (self.seed / "overlay").write_text("fork overlay\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "fork overlay")
        self.sha = self.git(self.seed, "rev-parse", "HEAD")
        self.env["RELEASE_SHA"] = self.sha
        self.origin = self.root / "origin.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.origin))
        self.work = self.root / "runner"
        self.git(self.root, "clone", str(self.origin), str(self.work))

    def git(self, cwd, *args):
        return subprocess.check_output(  # noqa: S603 - controlled local Git fixtures
            [REAL_GIT, "-C", str(cwd), *args],
            env=self.env,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def step(self, index, succeeds=True):
        result = subprocess.run(  # noqa: S603 - reviewed workflow shell; mocked network tools
            [REAL_BASH, "-c", STEPS[index]],
            cwd=self.work,
            env=self.env,
            text=True,
            capture_output=True,
        )
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def gh_calls(self):
        return (
            [json.loads(x) for x in self.calls.read_text().splitlines()]
            if self.calls.exists()
            else []
        )

    def state_update(self, **values):
        d = json.loads(self.state.read_text())
        d.update(values)
        self.state.write_text(json.dumps(d))

    def test_manual_only_trigger(self):
        # PyYAML treats the YAML 1.1 word "on" as True.
        triggers = CONFIG.get("on", CONFIG.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})

    def test_publisher_manual_only_and_version_tag_guard(self):
        publisher = yaml.safe_load(
            (ROOT / ".github/workflows/fork-release.yml").read_text()
        )
        triggers = publisher.get("on", publisher.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        guard = publisher["jobs"]["release"]["steps"][0]["run"]
        for ref_type, ref_name, passes in [
            ("tag", "v1.2.3", True),
            ("tag", "v2026.9.0-fork.2", True),
            ("branch", "v1.2.3", False),
            ("tag", "v1.2.3-rc.1", False),
            ("tag", "v1.2.3; touch injected", False),
        ]:
            with self.subTest(ref_type=ref_type, ref_name=ref_name):
                env = dict(self.env, REF_TYPE=ref_type, REF_NAME=ref_name)
                r = subprocess.run(  # noqa: S603 - reviewed guard; controlled fixture inputs
                    [REAL_BASH, "-c", guard],
                    cwd=self.work,
                    env=env,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(r.returncode == 0, passes, r.stderr)
        self.assertFalse((self.work / "injected").exists())

    def test_latest_failed_run_is_not_masked_by_earlier_success(self):
        self.state_update(
            runs=[
                {"status": "completed", "conclusion": "failure"},
                {"status": "completed", "conclusion": "success"},
            ]
        )
        self.step(0, succeeds=False)
        self.assertFalse(any(c[:2] == ["workflow", "run"] for c in self.gh_calls()))

    def test_inherited_publishers_are_restricted_to_exact_upstream(self):
        guarded = {
            "seerr-team/seerr": {
                "ci.yml": ["publish", "discord"],
                "release.yml": [
                    "create-draft-release",
                    "publish",
                    "sign",
                    "publish-release",
                ],
                "preview.yml": ["publish"],
            },
            "wizarrrr/wizarr": {
                "ci.yml": ["docker-dev"],
                "release.yml": ["stable-release"],
                "publish-manifest.yml": ["build"],
            },
        }
        required = "github.repository == '" + UPSTREAM + "'"
        for filename, jobs in guarded[UPSTREAM].items():
            config = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
            for job in jobs:
                with self.subTest(workflow=filename, job=job):
                    guard = config["jobs"][job].get("if", "")
                    self.assertTrue(
                        guard == required or guard.startswith(required + " && ("), guard
                    )
        fork_ci = yaml.safe_load(
            (ROOT / ".github/workflows/fork-build-test.yml").read_text()
        )
        self.assertIs(fork_ci["jobs"]["ci"]["with"]["publish_edge"], False)

    def test_exact_sha_release_and_dispatch(self):
        self.step(0)
        self.step(1)
        self.assertEqual(
            self.git(self.origin, "rev-parse", "v1.2.3-fork.1^{commit}"), self.sha
        )
        calls = self.gh_calls()
        run = next(c for c in calls if c[:2] == ["run", "list"])
        self.assertEqual(run[run.index("--commit") + 1], self.sha)
        dispatch = [c for c in calls if c[:2] == ["workflow", "run"]]
        self.assertEqual(len(dispatch), 1)
        self.assertEqual(dispatch[0][dispatch[0].index("--ref") + 1], "v1.2.3-fork.1")
        pushes = [
            json.loads(x)
            for x in self.git_calls.read_text().splitlines()
            if json.loads(x)[0] == "push"
        ]
        self.assertEqual(pushes, [["push", "origin", "refs/tags/v1.2.3-fork.1"]])

    def test_invalid_inputs_rejected_before_external_calls(self):
        for key, value in [
            ("UPSTREAM_TAG", "v1.2.3; touch injected"),
            ("RELEASE_TAG", "v1.2.30-fork.1"),
            ("RELEASE_SHA", "main"),
        ]:
            with self.subTest(key=key):
                prior = self.env[key]
                self.env[key] = value
                self.step(0, succeeds=False)
                self.env[key] = prior
                self.assertEqual(self.gh_calls(), [])
                self.assertFalse(self.git_calls.exists())
        self.assertFalse((self.work / "injected").exists())

    def test_prerelease_rejected(self):
        self.state_update(
            release={
                "tag_name": "v1.2.3",
                "draft": False,
                "prerelease": True,
                "published_at": "2026-09-13",
            }
        )
        self.step(0, succeeds=False)
        self.assertFalse(self.git_calls.exists())

    def test_failed_or_pending_ci_rejected(self):
        for status, conclusion in [("completed", "failure"), ("in_progress", "")]:
            with self.subTest(status=status):
                self.state_update(runs=[{"status": status, "conclusion": conclusion}])
                self.step(0, succeeds=False)
        self.assertFalse(any(c[:2] == ["workflow", "run"] for c in self.gh_calls()))

    def test_source_outside_default_branch_rejected(self):
        self.git(self.work, "checkout", "--detach", self.upstream_sha)
        (self.work / "alternative").write_text("not on approved base\n")
        self.git(self.work, "add", ".")
        self.git(self.work, "commit", "-m", "alternative")
        self.env["RELEASE_SHA"] = self.git(self.work, "rev-parse", "HEAD")
        self.step(0, succeeds=False)
        self.assertFalse(any(c[:2] == ["run", "list"] for c in self.gh_calls()))

    def test_missing_upstream_ancestry_rejected(self):
        self.git(self.seed, "checkout", "--orphan", "unrelated")
        self.git(self.seed, "commit", "-m", "unrelated upstream")
        self.git(self.seed, "tag", "v9.0.0")
        self.git(self.seed, "push", str(self.upstream), "refs/tags/v9.0.0")
        self.env.update(UPSTREAM_TAG="v9.0.0", RELEASE_TAG="v9.0.0")
        self.state_update(
            release={
                "tag_name": "v9.0.0",
                "draft": False,
                "prerelease": False,
                "published_at": "2026-09-13",
            }
        )
        self.step(0, succeeds=False)
        self.assertFalse(any(c[:2] == ["run", "list"] for c in self.gh_calls()))

    def test_existing_tag_is_never_moved_or_redispatched(self):
        self.git(self.origin, "tag", "v1.2.3-fork.1", self.upstream_sha)
        self.step(0)
        self.step(1, succeeds=False)
        self.assertEqual(
            self.git(self.origin, "rev-parse", "v1.2.3-fork.1"), self.upstream_sha
        )
        self.assertFalse(any(c[:2] == ["workflow", "run"] for c in self.gh_calls()))

    def test_publisher_blob_mismatch_rejected(self):
        p = self.seed / ".github/workflows/fork-release.yml"
        p.write_text(p.read_text() + "\n# changed publisher\n")
        self.git(self.seed, "commit", "-am", "publisher policy update")
        self.git(self.seed, "push", str(self.origin), "HEAD:refs/heads/" + BASE)
        self.git(self.work, "fetch", "origin")
        self.step(0, succeeds=False)
        self.assertFalse(any(c[:2] == ["workflow", "run"] for c in self.gh_calls()))


if __name__ == "__main__":
    unittest.main()
