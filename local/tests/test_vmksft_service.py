#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import dataclasses
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
LIB_DIR = TESTS_DIR.parent / "lib"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from vmksft_queue import build_options, parse_args  # noqa: E402
from vmksft_service import VmksftService  # noqa: E402
from vmksft_job_lib import build_executor_config, create_relative_symlink, create_run_layout  # noqa: E402
from vmksft_service_lib import (  # noqa: E402
    InjectFile,
    JobOptions,
    RuntimeConfig,
    cancel_queued_job,
    enqueue_job,
    ensure_layout,
    iter_job_records,
    load_job_record,
    next_queued_job,
    patch_has_diff,
    recover_stale_running_jobs,
    write_public_status,
)


class VmksftServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.script_dir = self.root / "local"
        self.script_dir.mkdir(parents=True)
        self.kernel_tree = self.root / "kernel"
        self.patch_dir = self.root / "patches"
        self.patch_dir.mkdir()
        self._init_kernel_repo(self.kernel_tree)

        self.config = RuntimeConfig(
            script_dir=self.script_dir,
            kernel_tree=self.kernel_tree,
            harness_state_dir=self.root / "state" / "vmksft-net",
            public_host="localhost",
            web_port=8888,
            targets="net net/packetdrill drivers/net/netdevsim",
            skip_tests="",
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

    def test_enqueue_committed_job_overlays_injected_file(self):
        inject_source = self.root / "inject-helper.sh"
        inject_source.write_text("#!/bin/sh\necho injected\n", encoding="utf-8")

        record = enqueue_job(
            self.config,
            JobOptions(
                mode="committed",
                inject_files=(
                    InjectFile(
                        source=str(inject_source),
                        destination=str(self.kernel_tree / "tools/testing/selftests/net") + "/",
                    ),
                ),
            ),
        )

        snapshot_tree = Path(record["snapshot_tree"])
        injected = snapshot_tree / "tools/testing/selftests/net/inject-helper.sh"

        self.assertTrue(injected.is_file())
        self.assertEqual(injected.read_text(encoding="utf-8"), inject_source.read_text(encoding="utf-8"))
        self.assertNotEqual(record["snapshot_head"], record["source_head"])
        self.assertEqual(record["inject_count"], 1)
        self.assertEqual(record["inject_destinations"], ["tools/testing/selftests/net/inject-helper.sh"])

    def test_enqueue_dirty_job_applies_injected_file_last(self):
        tracked = self.kernel_tree / "tools/testing/selftests/net/sample.sh"
        tracked.write_text("#!/bin/sh\necho dirty\n", encoding="utf-8")
        inject_source = self.root / "dirty-override.sh"
        inject_source.write_text("#!/bin/sh\necho injected last\n", encoding="utf-8")

        record = enqueue_job(
            self.config,
            JobOptions(
                mode="dirty",
                inject_files=(
                    InjectFile(source=str(inject_source), destination=str(tracked)),
                ),
            ),
        )

        snapshot_tree = Path(record["snapshot_tree"])
        snapshot_file = snapshot_tree / "tools/testing/selftests/net/sample.sh"

        self.assertEqual(snapshot_file.read_text(encoding="utf-8"), inject_source.read_text(encoding="utf-8"))
        self.assertNotEqual(record["snapshot_head"], record["source_head"])

    def test_enqueue_patches_job_freezes_explicit_patch_dir(self):
        tracked = self.kernel_tree / "tools/testing/selftests/net/sample.sh"
        tracked.write_text("#!/bin/sh\necho queued patch\n", encoding="utf-8")
        patch_path = self.patch_dir / "0001-sample.patch"
        with patch_path.open("wb") as fp:
            subprocess.run(
                ["git", "-C", str(self.kernel_tree), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
                check=True,
                stdout=fp,
                stderr=subprocess.PIPE,
            )

        record = enqueue_job(self.config, JobOptions(mode="patches", patch_dir=str(self.patch_dir)))

        tracked.write_text("#!/bin/sh\necho changed later\n", encoding="utf-8")
        patch_path.write_text("not a patch anymore\n", encoding="utf-8")

        snapshot_tree = Path(record["snapshot_tree"])
        snapshot_file = snapshot_tree / "tools/testing/selftests/net/sample.sh"
        self.assertIn("queued patch", snapshot_file.read_text(encoding="utf-8"))
        self.assertEqual(record["patch_dir"], str(self.patch_dir))
        self.assertEqual(record["patch_count"], 1)

    def test_enqueue_patches_job_applies_injected_file_after_patch_series(self):
        tracked = self.kernel_tree / "tools/testing/selftests/net/sample.sh"
        tracked.write_text("#!/bin/sh\necho queued patch\n", encoding="utf-8")
        patch_path = self.patch_dir / "0001-sample.patch"
        with patch_path.open("wb") as fp:
            subprocess.run(
                ["git", "-C", str(self.kernel_tree), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
                check=True,
                stdout=fp,
                stderr=subprocess.PIPE,
            )

        inject_source = self.root / "patch-override.sh"
        inject_source.write_text("#!/bin/sh\necho injected after patches\n", encoding="utf-8")

        record = enqueue_job(
            self.config,
            JobOptions(
                mode="patches",
                patch_dir=str(self.patch_dir),
                inject_files=(
                    InjectFile(source=str(inject_source), destination=str(tracked)),
                ),
            ),
        )

        snapshot_tree = Path(record["snapshot_tree"])
        snapshot_file = snapshot_tree / "tools/testing/selftests/net/sample.sh"

        self.assertEqual(snapshot_file.read_text(encoding="utf-8"), inject_source.read_text(encoding="utf-8"))
        self.assertEqual(record["patch_count"], 1)
        self.assertEqual(record["inject_destinations"], ["tools/testing/selftests/net/sample.sh"])

    def test_enqueue_patches_job_requires_explicit_patch_dir(self):
        with self.assertRaisesRegex(ValueError, "--patch-dir is required"):
            enqueue_job(self.config, JobOptions(mode="patches"))

    def test_enqueue_job_last_injected_file_wins_for_duplicate_destination(self):
        first = self.root / "first.sh"
        first.write_text("#!/bin/sh\necho first\n", encoding="utf-8")
        second = self.root / "second.sh"
        second.write_text("#!/bin/sh\necho second\n", encoding="utf-8")
        destination = str(self.kernel_tree / "tools/testing/selftests/net/override.sh")

        record = enqueue_job(
            self.config,
            JobOptions(
                inject_files=(
                    InjectFile(source=str(first), destination=destination),
                    InjectFile(source=str(second), destination=destination),
                ),
            ),
        )

        snapshot_tree = Path(record["snapshot_tree"])
        snapshot_file = snapshot_tree / "tools/testing/selftests/net/override.sh"

        self.assertEqual(snapshot_file.read_text(encoding="utf-8"), second.read_text(encoding="utf-8"))
        self.assertEqual(
            record["inject_destinations"],
            ["tools/testing/selftests/net/override.sh", "tools/testing/selftests/net/override.sh"],
        )

    def test_enqueue_job_freezes_service_skip_tests(self):
        config = dataclasses.replace(self.config, skip_tests="net:skip-one.sh skip-two.sh")
        first = enqueue_job(config, JobOptions())
        self.assertEqual(first["skip_tests"], "net:skip-one.sh skip-two.sh")

        updated = load_job_record(config, first["job_id"])
        self.assertEqual(updated["skip_tests"], "net:skip-one.sh skip-two.sh")

        changed = dataclasses.replace(config, skip_tests="net:skip-three.sh")
        second = enqueue_job(changed, JobOptions())
        self.assertEqual(second["skip_tests"], "net:skip-three.sh")

        unchanged = load_job_record(config, first["job_id"])
        self.assertEqual(unchanged["skip_tests"], "net:skip-one.sh skip-two.sh")

    def test_enqueue_job_freezes_service_targets_and_selected_tests(self):
        config = dataclasses.replace(self.config, targets="net net/mptcp")
        first = enqueue_job(config, JobOptions(tests="net:sample.sh"))
        self.assertEqual(first["targets"], "net net/mptcp")
        self.assertEqual(first["selected_tests"], "net:sample.sh")

        changed = dataclasses.replace(config, targets="net/packetdrill")
        second = enqueue_job(changed, JobOptions(tests="net/packetdrill:bar_case.pkt"))
        self.assertEqual(second["targets"], "net/packetdrill")
        self.assertEqual(second["selected_tests"], "net/packetdrill:bar_case.pkt")

        unchanged = load_job_record(config, first["job_id"])
        self.assertEqual(unchanged["targets"], "net net/mptcp")
        self.assertEqual(unchanged["selected_tests"], "net:sample.sh")

    def test_build_executor_config_uses_frozen_targets_and_selected_tests(self):
        record = enqueue_job(
            dataclasses.replace(self.config, targets="net net/mptcp"),
            JobOptions(tests="net:sample.sh"),
        )
        layout = create_run_layout(self.config, "run-config-test")
        layout.run_dir.mkdir(parents=True)

        build_executor_config(
            self.config,
            layout,
            record,
            self.kernel_tree,
            "local-vmksft-net-committed-run-config-test",
            "2026-03-23T00:00:00+00:00",
        )

        config_text = layout.config_path.read_text(encoding="utf-8")
        self.assertIn("test = net net/mptcp", config_text)
        self.assertIn("target = net net/mptcp", config_text)
        self.assertIn("only_tests = net:sample.sh", config_text)

    def test_patch_has_diff_ignores_cover_letter_separator(self):
        cover = self.patch_dir / "0000-cover-letter.patch"
        cover.write_text(
            "From 0123456789abcdef0123456789abcdef01234567 Mon Sep 17 00:00:00 2001\n"
            "Subject: [PATCH 0/1] cover letter\n"
            "\n"
            "Summary only.\n"
            "---\n"
            " 1 file changed, 1 insertion(+)\n",
            encoding="utf-8",
        )

        self.assertFalse(patch_has_diff(cover))

    def test_enqueue_job_rejects_missing_injected_source(self):
        with self.assertRaisesRegex(FileNotFoundError, "inject source not found"):
            enqueue_job(
                self.config,
                JobOptions(
                    inject_files=(
                        InjectFile(
                            source=str(self.root / "missing.txt"),
                            destination=str(self.kernel_tree / "tools/testing/selftests/net/sample.sh"),
                        ),
                    ),
                ),
            )

    def test_enqueue_job_rejects_directory_injected_source(self):
        with self.assertRaisesRegex(ValueError, "inject source must be a file or symlink"):
            enqueue_job(
                self.config,
                JobOptions(
                    inject_files=(
                        InjectFile(
                            source=str(self.patch_dir),
                            destination=str(self.kernel_tree / "tools/testing/selftests/net/sample.sh"),
                        ),
                    ),
                ),
            )

    def test_enqueue_job_rejects_injected_destination_outside_kernel_tree(self):
        inject_source = self.root / "outside.sh"
        inject_source.write_text("#!/bin/sh\necho outside\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "inject destination must be under kernel tree"):
            enqueue_job(
                self.config,
                JobOptions(
                    inject_files=(
                        InjectFile(source=str(inject_source), destination=str(self.root / "outside-target.sh")),
                    ),
                ),
            )

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

    def test_service_build_job_command_uses_unified_entrypoint(self):
        record = enqueue_job(self.config, JobOptions(fresh_cache=True))

        command = VmksftService(self.config).build_job_command(record["job_id"])
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("/local/bin/vmksft_service.py"))
        self.assertEqual(command[2:], ["run-job", "--job-id", record["job_id"]])

    def test_queue_submit_parser_accepts_tests_option(self):
        args = parse_args(["submit", "--mode", "dirty", "--tests", "net:sample.sh"])
        options = build_options(args)

        self.assertEqual(options.mode, "dirty")
        self.assertEqual(options.tests, "net:sample.sh")

    def test_queue_submit_parser_accepts_repeated_injected_files(self):
        args = parse_args([
            "submit",
            "--mode",
            "dirty",
            "--inject-file",
            "first",
            "/tmp/one",
            "--tests",
            "net:sample.sh",
            "--inject-file",
            "second",
            "/tmp/two/",
        ])
        options = build_options(args)

        self.assertEqual(options.mode, "dirty")
        self.assertEqual(options.tests, "net:sample.sh")
        self.assertEqual(
            options.inject_files,
            (
                InjectFile(source="first", destination="/tmp/one"),
                InjectFile(source="second", destination="/tmp/two/"),
            ),
        )

    def test_create_relative_symlink_replaces_stale_directory(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "stale-link"
        link.mkdir()
        (link / "old.txt").write_text("stale\n", encoding="utf-8")

        create_relative_symlink(link, target)

        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target)


if __name__ == "__main__":
    unittest.main()
