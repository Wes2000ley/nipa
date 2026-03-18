#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import os
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path


sys.path.insert(0, os.path.dirname(__file__))

from vmksft_service_lib import (  # noqa: E402
    JobOptions,
    RuntimeConfig,
    build_runner_command,
    cancel_queued_job,
    enqueue_job,
    ensure_layout,
    iter_job_records,
    load_job_record,
    next_queued_job,
    recover_stale_running_jobs,
    write_public_status,
)


class VmksftServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.script_dir = self.root / "local"
        self.script_dir.mkdir(parents=True)
        (self.script_dir / "run-vmksft-net.sh").write_text("#!/bin/bash\n", encoding="utf-8")
        self.kernel_tree = self.root / "kernel"
        self.patch_dir = self.root / "patches"
        self.patch_dir.mkdir()
        self._init_kernel_repo(self.kernel_tree)

        self.config = RuntimeConfig(
            script_dir=self.script_dir,
            repo_root=self.root,
            kernel_tree=self.kernel_tree,
            state_dir=self.root / "state",
            harness_state_dir=self.root / "state" / "vmksft-net",
            patch_dir=self.patch_dir,
            public_host="localhost",
            web_port=8888,
        )
        ensure_layout(self.config)

    def tearDown(self):
        self.tempdir.cleanup()

    def _run(self, *cmd, cwd=None):
        subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _init_kernel_repo(self, path):
        path.mkdir(parents=True)
        self._run("git", "init", "-q", str(path))
        self._run("git", "-C", str(path), "config", "user.name", "vmksft-test")
        self._run("git", "-C", str(path), "config", "user.email", "vmksft-test@example.com")
        target_dir = path / "tools/testing/selftests/net"
        target_dir.mkdir(parents=True)
        (target_dir / "sample.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self._run("git", "-C", str(path), "add", ".")
        self._run("git", "-C", str(path), "commit", "-q", "-m", "initial")

    def test_enqueue_jobs_preserves_fifo_order(self):
        first = enqueue_job(self.config, JobOptions())
        second = enqueue_job(self.config, JobOptions())

        records = iter_job_records(self.config)
        self.assertEqual([first["job_id"], second["job_id"]], [r["job_id"] for r in records])

        status = write_public_status(self.config)
        self.assertEqual(status["queue_depth"], 2)
        self.assertEqual(status["counts"]["queued"], 2)

    def test_enqueue_dirty_job_freezes_dirty_snapshot(self):
        tracked = self.kernel_tree / "tools/testing/selftests/net/sample.sh"
        tracked.write_text("#!/bin/sh\necho dirty\n", encoding="utf-8")
        untracked = self.kernel_tree / "tools/testing/selftests/net/extra.txt"
        untracked.write_text("extra\n", encoding="utf-8")

        record = enqueue_job(self.config, JobOptions(mode="dirty"))

        snapshot_tree = Path(record["snapshot_tree"])
        self.assertNotEqual(record["snapshot_head"], record["source_head"])
        self.assertTrue((snapshot_tree / "tools/testing/selftests/net/extra.txt").is_file())
        self.assertIn("dirty", tracked.read_text(encoding="utf-8"))
        self.assertEqual(record["requested_mode"], "dirty")

    def test_cancel_queued_job_marks_job_cancelled(self):
        record = enqueue_job(self.config, JobOptions())
        self.assertTrue(cancel_queued_job(self.config, record["job_id"]))

        updated = load_job_record(self.config, record["job_id"])
        self.assertEqual(updated["state"]["status"], "cancelled")
        self.assertTrue((self.config.service_root / "cancelled" / record["job_id"]).is_symlink())

    def test_recover_stale_running_job_marks_failed(self):
        record = enqueue_job(self.config, JobOptions())
        running = next_queued_job(self.config)
        self.assertEqual(running["job_id"], record["job_id"])
        self.assertEqual(load_job_record(self.config, record["job_id"])["state"]["status"], "running")

        recovered = recover_stale_running_jobs(self.config)
        self.assertEqual(recovered, [record["job_id"]])

        updated = load_job_record(self.config, record["job_id"])
        self.assertEqual(updated["state"]["status"], "failed")
        self.assertEqual(updated["state"]["run_exit_code"], 130)
        self.assertTrue((self.config.service_root / "failed" / record["job_id"]).is_symlink())

    def test_build_runner_command_uses_private_internal_port_and_job_meta(self):
        record = enqueue_job(self.config, JobOptions(fresh_cache=True))

        command = build_runner_command(self.config, record, 19001)
        self.assertIn("--internal-http-port", command)
        self.assertIn("19001", command)
        self.assertIn("--mode", command)
        self.assertIn("committed", command)
        self.assertIn("--job-meta", command)
        self.assertIn("--fresh-cache", command)


if __name__ == "__main__":
    unittest.main()
