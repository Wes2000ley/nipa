#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
BIN_DIR = TESTS_DIR.parent / "bin"
SCRIPT = BIN_DIR / "build-vmksft-history.py"
EXECUTOR = "vmksft-net-local"


class BuildVmksftHistoryTests(unittest.TestCase):
    def _run(self, *args):
        subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_main_does_not_generate_filters_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            state_dir = root / "state"
            site_root = root / "site"
            (state_dir / "runs").mkdir(parents=True)

            self._run(
                "--state-dir", str(state_dir),
                "--site-root", str(site_root),
                "--executor-name", EXECUTOR,
            )

            self.assertTrue((site_root / "history.json").is_file())
            self.assertTrue((site_root / "contest" / "all-results.json").is_file())
            self.assertFalse((site_root / "contest" / "filters.json").exists())

    def test_retry_output_url_is_emitted_for_retry_results(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            state_dir = root / "state"
            site_root = root / "site"
            run_id = "20260319-052309-94"
            run_dir = state_dir / "runs" / run_id
            exec_root = run_dir / "www" / EXECUTOR
            json_root = exec_root / "jsons"
            json_root.mkdir(parents=True)

            (run_dir / "www" / "run-meta.json").write_text(
                json.dumps({
                    "published_branch": "local-vmksft-net-committed-20260319-052309-94",
                    "source_branch": "committed",
                }),
                encoding="utf-8",
            )
            detail_url = (
                f"http://127.0.0.1:8888/runs/{run_id}/{EXECUTOR}/jsons/results-564963.json"
            )
            (json_root / "results.json").write_text(
                json.dumps([{"url": detail_url}]),
                encoding="utf-8",
            )
            (json_root / "results-564963.json").write_text(
                json.dumps({
                    "executor": EXECUTOR,
                    "branch": "local-vmksft-net-committed-20260319-052309-94",
                    "start": "2026-03-19 05:23:16.250398+00:00",
                    "end": "2026-03-19 05:47:22.590754+00:00",
                    "results": [{
                        "group": "selftests-net",
                        "link": (
                            f"http://127.0.0.1:8888/runs/{run_id}/{EXECUTOR}/results/564963/122-txtimestamp-sh"
                        ),
                        "result": "fail",
                        "retry": "pass",
                        "test": "txtimestamp-sh",
                        "time": 3.638058,
                    }],
                }),
                encoding="utf-8",
            )

            self._run(
                "--state-dir", str(state_dir),
                "--site-root", str(site_root),
                "--executor-name", EXECUTOR,
            )

            with open(site_root / "contest" / "all-results.json", "r", encoding="utf-8") as fp:
                rows = json.load(fp)

            self.assertEqual(len(rows), 1)
            result = rows[0]["results"][0]
            self.assertEqual(
                result["retry_output_url"],
                (
                    f"http://127.0.0.1:8888/runs/{run_id}/{EXECUTOR}/results/564963/"
                    "122-txtimestamp-sh-retry/stdout"
                ),
            )


if __name__ == "__main__":
    unittest.main()
