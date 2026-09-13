#!/usr/bin/env python3
"""Offline regressions against the workflow's actual shell steps."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/fork-sync-upstream.yml'
CONFIG = yaml.safe_load(WORKFLOW.read_text())
STEPS = [s for s in CONFIG['jobs']['sync']['steps'] if 'run' in s]
BASE_BRANCH = CONFIG['env']['BASE_BRANCH']
UPSTREAM_REPO = CONFIG['env']['UPSTREAM_REPO']
REAL_GIT = shutil.which('git')


class SyncWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.state = self.root / 'gh-state.json'
        self.calls = self.root / 'gh-calls.jsonl'
        self.git_calls = self.root / 'git-calls.jsonl'
        self.state.write_text(json.dumps({'release': {'tag_name': 'v1.2.3', 'draft': False,
                                                     'prerelease': False, 'published_at': '2026-09-13'}}))
        (self.bin / 'gh').write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
p = Path(os.environ['FAKE_GH_STATE'])
s = json.loads(p.read_text())
with open(os.environ['FAKE_GH_CALLS'], 'a') as f: f.write(json.dumps(args)+'\\n')
if args[0] == 'api': print(json.dumps(s['release']))
elif args[:2] == ['pr', 'list']: print(json.dumps(s.get('prs', [])))
elif args[:2] == ['pr', 'create']: s['prs'] = [{'number': 1, 'state': 'OPEN'}]
elif args[:2] == ['run', 'list']: print(json.dumps(s.get('runs', [])))
elif args[:2] == ['issue', 'list']: print(json.dumps(s.get('issues', [])))
elif args[:2] == ['issue', 'create']:
    s['issues'] = [{'number': 14, 'title': args[args.index('--title') + 1]}]
elif args[:2] not in [['label', 'create'], ['workflow', 'run']]:
    raise SystemExit('unexpected gh call')
p.write_text(json.dumps(s))
''')
        (self.bin / 'git').write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FAKE_GIT_CALLS'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
os.execv(os.environ['REAL_GIT'], [os.environ['REAL_GIT']] + sys.argv[1:])
''')
        for p in self.bin.iterdir():
            p.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FAKE_GH_STATE=str(self.state), FAKE_GH_CALLS=str(self.calls),
                        FAKE_GIT_CALLS=str(self.git_calls), REAL_GIT=REAL_GIT,
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid',
                        GITHUB_REPOSITORY='NotCavnfox/' + UPSTREAM_REPO.rsplit('/', 1)[1], GITHUB_RUN_ID='123',
                        BASE_BRANCH=BASE_BRANCH, UPSTREAM_REPO=UPSTREAM_REPO, REQUESTED_REF='')
        self.counter = 0

    def git(self, directory, *args):
        return subprocess.check_output([REAL_GIT, '-C', str(directory), *args], env=self.env,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def histories(self, conflict=False):
        seed = self.root / 'seed'
        seed.mkdir()
        self.git(seed, 'init', '-b', BASE_BRANCH)
        (seed / 'common').write_text('base\n')
        self.git(seed, 'add', '.')
        self.git(seed, 'commit', '-m', 'base')
        base = self.git(seed, 'rev-parse', 'HEAD')
        (seed / 'common').write_text('upstream\n')
        self.git(seed, 'commit', '-am', 'stable update')
        self.git(seed, 'tag', 'v1.2.3')
        self.upstream = self.root / 'upstream.git'
        self.git(self.root, 'clone', '--bare', str(seed), str(self.upstream))
        self.git(seed, 'checkout', '-b', 'fork', base)
        (seed / ('common' if conflict else 'overlay')).write_text('fork patch\n')
        self.git(seed, 'add', '.')
        self.git(seed, 'commit', '-m', 'fork patch')
        self.origin = self.root / 'origin.git'
        self.git(self.root, 'init', '--bare', str(self.origin))
        self.git(seed, 'push', str(self.origin), 'HEAD:refs/heads/' + BASE_BRANCH)
        self.env['UPSTREAM_URL'] = str(self.upstream)
        self.runner()

    def runner(self):
        self.counter += 1
        self.work = self.root / ('runner-' + str(self.counter))
        self.git(self.root, 'clone', '--branch', BASE_BRANCH, str(self.origin), str(self.work))
        self.output = self.root / ('output-' + str(self.counter))
        self.env['GITHUB_OUTPUT'] = str(self.output)

    def run_step(self, index, expected=0):
        result = subprocess.run(['bash', '-c', STEPS[index]['run']], cwd=self.work,
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if self.output.exists():
            for line in self.output.read_text().splitlines():
                key, value = line.split('=', 1)
                self.env[key.upper()] = value
        return result

    def prepare(self):
        for i in range(4):
            self.run_step(i)

    def gh_calls(self):
        return [json.loads(s) for s in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def patch_state(self, **values):
        data = json.loads(self.state.read_text())
        data.update(values)
        self.state.write_text(json.dumps(data))

    def test_malformed_override_never_contacts_api_or_git(self):
        self.histories()
        self.env['REQUESTED_REF'] = 'v1.2.3; touch injected'
        self.run_step(1, expected=1)
        self.assertEqual(self.gh_calls(), [])
        self.assertFalse(self.git_calls.exists())
        self.assertFalse((self.work / 'injected').exists())

    def test_prerelease_override_rejected_before_fetch(self):
        self.histories()
        self.env['REQUESTED_REF'] = 'v1.2.3'
        self.patch_state(release={'tag_name': 'v1.2.3', 'draft': False,
                                  'prerelease': True, 'published_at': '2026-09-13'})
        self.run_step(1, expected=4)
        self.assertFalse(self.git_calls.exists())

    def test_calendar_stable_release_is_accepted(self):
        self.histories()
        self.git(self.upstream, 'tag', 'v2026.9.0', 'v1.2.3')
        self.env['REQUESTED_REF'] = 'v2026.9.0'
        self.patch_state(release={'tag_name': 'v2026.9.0', 'draft': False,
                                  'prerelease': False, 'published_at': '2026-09-13'})
        self.run_step(1)
        self.assertEqual(self.env['REF'], 'v2026.9.0')
        self.assertEqual(self.env['UPSTREAM_SHA'], self.git(self.upstream, 'rev-parse', 'v2026.9.0'))

    def test_clean_sync_retry_preserves_head_and_successful_ci(self):
        self.histories()
        self.prepare()
        self.run_step(4)
        branch = self.env['BRANCH']
        head = self.git(self.origin, 'rev-parse', 'refs/heads/' + branch)
        self.patch_state(runs=[{'status': 'completed', 'conclusion': 'success'}])
        self.runner()
        self.prepare()
        self.run_step(4)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/heads/' + branch), head)
        calls = self.gh_calls()
        self.assertEqual(sum(c[:2] == ['pr', 'create'] for c in calls), 1)
        self.assertEqual(sum(c[:2] == ['workflow', 'run'] for c in calls), 1)
        listed = [c for c in calls if c[:2] == ['run', 'list']]
        self.assertTrue(all(c[c.index('--commit') + 1] == head for c in listed))
        pushes = [json.loads(c) for c in self.git_calls.read_text().splitlines()
                  if json.loads(c)[0] == 'push']
        self.assertEqual(len(pushes), 1)
        self.assertFalse(any(a.startswith('-f') or a.startswith('+') for c in pushes for a in c[1:]))
        self.assertFalse(any(c[:2] == ['pr', 'merge'] for c in calls))

    def test_running_ci_not_dispatched_again(self):
        self.histories()
        self.prepare()
        self.patch_state(runs=[{'status': 'in_progress', 'conclusion': ''}])
        self.run_step(4)
        self.assertFalse(any(c[:2] == ['workflow', 'run'] for c in self.gh_calls()))

    def test_divergent_existing_branch_is_preserved(self):
        self.histories()
        self.prepare()
        self.run_step(4)
        branch = self.env['BRANCH']
        (self.work / 'unexpected').write_text('do not overwrite\n')
        self.git(self.work, 'add', '.')
        self.git(self.work, 'commit', '-m', 'other work')
        self.git(self.work, 'push', 'origin', 'HEAD:refs/heads/' + branch)
        divergent = self.git(self.origin, 'rev-parse', 'refs/heads/' + branch)
        self.runner()
        self.prepare()
        self.run_step(4, expected=1)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/heads/' + branch), divergent)

    def test_conflict_issue_reused_without_comment(self):
        self.histories(conflict=True)
        self.prepare()
        self.assertEqual(self.env['STATUS'], 'conflict')
        self.run_step(5, expected=1)
        self.run_step(5, expected=1)
        calls = self.gh_calls()
        self.assertEqual(sum(c[:2] == ['issue', 'create'] for c in calls), 1)
        self.assertFalse(any(c[:2] == ['issue', 'comment'] for c in calls))
        self.assertEqual(self.git(self.work, 'status', '--porcelain'), '')

    def test_closed_pr_decision_is_preserved(self):
        self.histories()
        self.prepare()
        self.patch_state(prs=[{'number': 1, 'state': 'CLOSED'}])
        self.run_step(4)
        self.assertFalse(any(c[:2] in [['pr', 'create'], ['workflow', 'run']] for c in self.gh_calls()))


if __name__ == '__main__':
    unittest.main()
