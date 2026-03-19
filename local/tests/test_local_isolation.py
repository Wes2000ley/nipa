#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LOCAL_ROOT = TESTS_DIR.parent
BIN_DIR = LOCAL_ROOT / "bin"
LIB_DIR = LOCAL_ROOT / "lib"

RUNTIME_FILES = [
    *sorted(BIN_DIR.glob("*.py")),
    *sorted(LIB_DIR.glob("*.py")),
    LOCAL_ROOT / "Dockerfile",
    LOCAL_ROOT / "docker-compose.yml",
    LOCAL_ROOT / "vmksft",
]

FORBIDDEN_SNIPPETS = (
    "contest/remote",
    "REMOTE_DIR",
    "NIPA_ROOT",
    "/workspace/nipa",
    "repo_root",
)


class LocalIsolationTests(unittest.TestCase):
    def test_runtime_sources_do_not_reference_non_local_repo_paths(self):
        for path in RUNTIME_FILES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for snippet in FORBIDDEN_SNIPPETS:
                self.assertNotIn(snippet, text, f"{path} still references {snippet!r}")

    def test_runtime_modules_import_with_local_paths_only(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(BIN_DIR), str(LIB_DIR)])

        code = (
            "import crash, host_scheduler, local_vm, local_vmksft_p, naming, "
            "results, vm_base, vmksft_http, vmksft_job_lib, vmksft_queue, vmksft_service, vmksft_service_lib"
        )

        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
