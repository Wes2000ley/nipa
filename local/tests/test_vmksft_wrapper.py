#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest

from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LOCAL_ROOT = TESTS_DIR.parent
WRAPPER = LOCAL_ROOT / "vmksft"

FAKE_DOCKER = """#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys

from pathlib import Path


root = Path(os.environ["FAKE_DOCKER_ROOT"])
log_path = Path(os.environ["FAKE_DOCKER_LOG"])


def map_path(value):
    if value.startswith("/"):
        return root / value.lstrip("/")
    return Path(value)


def remove_path(path):
    target = map_path(path)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    elif target.exists() or target.is_symlink():
        target.unlink()


def append_log(entry):
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, sort_keys=True) + "\\n")


def strip_compose_options(args):
    idx = 0
    while idx < len(args):
        if args[idx] in ("-f", "--env-file"):
            idx += 2
            continue
        return args[idx:]
    return []


args = sys.argv[1:]
if not args or args[0] != "compose":
    raise SystemExit("expected docker compose")

args = strip_compose_options(args[1:])
if not args:
    raise SystemExit("missing compose subcommand")

cmd = args[0]
if cmd == "ps":
    if args[1:] == ["--status", "running", "--services"]:
        print("vmksft-service")
        raise SystemExit(0)
    raise SystemExit(f"unexpected ps args: {args[1:]}")

if cmd != "exec":
    raise SystemExit(f"unexpected compose subcommand: {cmd}")

idx = 1
if args[idx] == "-T":
    idx += 1
service = args[idx]
idx += 1
run = args[idx:]
if service != "vmksft-service":
    raise SystemExit(f"unexpected service: {service}")

if run[:2] == ["mkdir", "-p"]:
    for item in run[2:]:
        map_path(item).mkdir(parents=True, exist_ok=True)
    raise SystemExit(0)

if run[:2] == ["rm", "-rf"]:
    for item in run[2:]:
        remove_path(item)
    raise SystemExit(0)

if len(run) >= 5 and run[:3] == ["tar", "-xf", "-"] and run[3] == "-C":
    destination = map_path(run[4])
    destination.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["tar", "-xf", "-", "-C", str(destination)],
        stdin=sys.stdin.buffer,
        check=False,
    )
    raise SystemExit(proc.returncode)

if run[:2] == ["python3", "/workspace/local/bin/vmksft_queue.py"]:
    queue_args = run[2:]
    inject_pairs = []
    idx = 0
    while idx < len(queue_args):
        if queue_args[idx] == "--inject-file":
            source = queue_args[idx + 1]
            destination = queue_args[idx + 2]
            mapped_source = map_path(source)
            inject_pairs.append(
                {
                    "source": source,
                    "destination": destination,
                    "source_exists": mapped_source.exists() or mapped_source.is_symlink(),
                    "source_text": mapped_source.read_text(encoding="utf-8"),
                }
            )
            idx += 3
            continue
        idx += 1

    append_log({"queue_args": queue_args, "inject_pairs": inject_pairs})
    print("queued fake-job status=queued mode=fake")
    raise SystemExit(0)

raise SystemExit(f"unexpected exec payload: {run}")
"""


class VmksftWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()
        self.fake_docker_root = self.root / "fake-docker-root"
        self.fake_docker_log = self.root / "fake-docker-log.jsonl"
        self.kernel_tree = self.root / "kernel"
        (self.kernel_tree / "tools/testing/selftests/net").mkdir(parents=True)
        self.inject_source = self.root / "helper.sh"
        self.inject_source.write_text("#!/bin/sh\necho wrapper\n", encoding="utf-8")

        docker_path = self.fake_bin / "docker"
        docker_path.write_text(textwrap.dedent(FAKE_DOCKER), encoding="utf-8")
        docker_path.chmod(docker_path.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tempdir.cleanup()

    def _run_wrapper(self, *args):
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([str(self.fake_bin), env.get("PATH", "")])
        env["NIPA_KERNEL_TREE"] = str(self.kernel_tree)
        env["NIPA_STATE_DIR"] = str(self.root / "state")
        env["FAKE_DOCKER_ROOT"] = str(self.fake_docker_root)
        env["FAKE_DOCKER_LOG"] = str(self.fake_docker_log)
        return subprocess.run(
            [str(WRAPPER), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_wrapper_stages_injected_files_and_rewrites_destination_under_container_kernel_tree(self):
        destination = str(self.kernel_tree / "tools/testing/selftests/net") + "/"

        proc = self._run_wrapper(
            "dirty",
            "--tests",
            "net:sample.sh",
            "--inject-file",
            str(self.inject_source),
            destination,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("queued fake-job", proc.stdout)

        entries = [
            json.loads(line)
            for line in self.fake_docker_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(entries), 1)

        entry = entries[0]
        self.assertEqual(entry["queue_args"][:4], ["submit", "--mode", "dirty", "--tests"])
        self.assertEqual(entry["queue_args"][4], "net:sample.sh")
        self.assertEqual(len(entry["inject_pairs"]), 1)

        inject_pair = entry["inject_pairs"][0]
        self.assertTrue(inject_pair["source"].startswith("/tmp/local-vmksft-uploaded-files/"))
        self.assertTrue(inject_pair["source_exists"])
        self.assertEqual(inject_pair["source_text"], self.inject_source.read_text(encoding="utf-8"))
        self.assertEqual(
            inject_pair["destination"],
            "/workspace/kernel/tools/testing/selftests/net/helper.sh",
        )

        staged_source = self.fake_docker_root / inject_pair["source"].lstrip("/")
        self.assertFalse(staged_source.exists())
        self.assertFalse(staged_source.parent.parent.exists())


if __name__ == "__main__":
    unittest.main()
