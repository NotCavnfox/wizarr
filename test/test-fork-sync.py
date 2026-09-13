#!/usr/bin/env python3
"""The shared repository tests implementation; this fork verifies its caller."""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / ".github/workflows/fork-sync-upstream.yml").read_text())


class SyncCaller(unittest.TestCase):
    def test_reviewed_immutable_shared_reference(self):
        self.assertRegex(
            CONFIG["jobs"]["sync"]["uses"],
            r"^NotCavnfox/\.github/\.github/workflows/sync-upstream\.yml@[0-9a-f]{40}$",
        )

    def test_caller_identity_and_authority(self):
        job = CONFIG["jobs"]["sync"]
        self.assertEqual(set(CONFIG["jobs"]), {"sync"})
        self.assertEqual(
            job["with"],
            {
                "upstream_repo": "wizarrrr/wizarr",
                "base_branch": "main",
                "upstream_ref": "${{ inputs.upstream_ref }}",
            },
        )
        self.assertNotIn("secrets", job)
        self.assertEqual(
            CONFIG["permissions"],
            {
                "contents": "write",
                "pull-requests": "write",
                "issues": "write",
                "actions": "write",
            },
        )

    def test_existing_schedule_and_manual_dispatch_only(self):
        triggers = CONFIG.get("on", CONFIG.get(True))
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        self.assertEqual(triggers["schedule"], [{"cron": "17 6 * * *"}])
        self.assertEqual(
            triggers["workflow_dispatch"]["inputs"]["upstream_ref"]["default"], ""
        )


if __name__ == "__main__":
    unittest.main()
