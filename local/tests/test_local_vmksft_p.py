#!/usr/bin/env python3

import os
import tempfile
import sys
import unittest
from unittest import mock
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LIB_DIR = TESTS_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_vmksft_p import (  # noqa: E402
    build_nstat_history_path,
    build_test_command,
    filter_prog_list,
    load_runtime_history,
    main,
    parse_test_selectors,
    parse_skip_tests,
    run_executor,
    runtime_history_key,
    save_runtime_history,
    select_prog_list,
    sort_progs_by_runtime_history,
    update_runtime_history,
)


class LocalVmksftPTests(unittest.TestCase):
    def test_build_test_command_matches_upstream_vmksft_p_shape(self):
        cmd = build_test_command("tools/testing/selftests",
                                 "net/packetdrill",
                                 "tcp_rcv_toobig.pkt")

        self.assertIn("TEST_PROGS=tcp_rcv_toobig.pkt", cmd)
        self.assertIn("TEST_GEN_PROGS=", cmd)
        self.assertIn('TARGETS="net/packetdrill"', cmd)
        self.assertNotIn("TEST_CUSTOM_PROGS=", cmd)

    def test_build_test_command_treats_generated_binary_like_upstream(self):
        cmd = build_test_command("tools/testing/selftests",
                                 "net",
                                 "epoll_busy_poll")

        self.assertIn("TEST_PROGS=epoll_busy_poll", cmd)
        self.assertIn('TARGETS="net"', cmd)

    def test_build_nstat_history_path_isolated_per_test(self):
        path = build_nstat_history_path(3, 107, "tcp_fastopen_backup_key.sh")

        self.assertEqual(path, "/tmp/nipa-nstat-thr3-107-tcp-fastopen-backup-key-sh")

    def test_build_nstat_history_path_distinguishes_retries(self):
        path = build_nstat_history_path(3, 107, "tcp_fastopen_backup_key.sh", is_retry=True)

        self.assertEqual(path, "/tmp/nipa-nstat-thr3-107-tcp-fastopen-backup-key-sh-retry")

    def test_filter_prog_list_removes_configured_skips_before_queueing(self):
        progs = [
            ("net", "tcp_fastopen_backup_key.sh"),
            ("net", "other_test.sh"),
            ("net/packetdrill", "bar_case.pkt"),
        ]
        skip_tests = parse_skip_tests(
            "tcp_fastopen_backup_key.sh net/packetdrill:bar-case-pkt"
        )

        kept, skipped = filter_prog_list(progs, skip_tests)

        self.assertEqual(kept, [("net", "other_test.sh")])
        self.assertEqual(
            skipped,
            [
                ("net", "tcp_fastopen_backup_key.sh"),
                ("net/packetdrill", "bar_case.pkt"),
            ],
        )

    def test_parse_test_selectors_supports_whitespace_and_commas(self):
        selectors = parse_test_selectors(" net:foo.sh,bar_case.pkt   baz ")

        self.assertEqual(selectors, {"net:foo.sh", "bar_case.pkt", "baz"})

    def test_select_prog_list_keeps_only_matching_programs(self):
        progs = [
            ("net", "tcp_fastopen_backup_key.sh"),
            ("net", "other_test.sh"),
            ("net/packetdrill", "bar_case.pkt"),
        ]

        kept, excluded = select_prog_list(
            progs,
            parse_test_selectors("net:tcp_fastopen_backup_key.sh bar_case.pkt"),
        )

        self.assertEqual(
            kept,
            [
                ("net", "tcp_fastopen_backup_key.sh"),
                ("net/packetdrill", "bar_case.pkt"),
            ],
        )
        self.assertEqual(excluded, [("net", "other_test.sh")])

    def test_select_prog_list_matches_namified_target_selector(self):
        progs = [
            ("net/packetdrill", "bar_case.pkt"),
            ("net/packetdrill", "other_case.pkt"),
        ]

        kept, excluded = select_prog_list(
            progs,
            parse_test_selectors("net/packetdrill:bar-case-pkt"),
        )

        self.assertEqual(kept, [("net/packetdrill", "bar_case.pkt")])
        self.assertEqual(excluded, [("net/packetdrill", "other_case.pkt")])

    def test_run_executor_requires_explicit_generated_config(self):
        with self.assertRaisesRegex(ValueError, "explicit config path"):
            run_executor(None)

    def test_main_without_config_exits_with_usage_instead_of_name_error(self):
        with mock.patch.object(sys, "argv", ["local_vmksft_p.py"]):
            with self.assertRaisesRegex(SystemExit, "usage: local_vmksft_p.py"):
                main()

    def test_sort_progs_by_runtime_history_front_loads_long_tests(self):
        progs = [
            ("net", "short_a.sh"),
            ("net", "long.sh"),
            ("net", "short_b.sh"),
            ("net", "medium.sh"),
        ]
        history = {
            runtime_history_key("net", "short_a.sh"): 2.0,
            runtime_history_key("net", "long.sh"): 95.0,
            runtime_history_key("net", "short_b.sh"): 4.0,
            runtime_history_key("net", "medium.sh"): 18.0,
        }

        ordered = sort_progs_by_runtime_history(progs, history, cutoff_sec=10)

        self.assertEqual(
            ordered,
            [
                ("net", "long.sh"),
                ("net", "medium.sh"),
                ("net", "short_a.sh"),
                ("net", "short_b.sh"),
            ],
        )

    def test_sort_progs_by_runtime_history_keeps_short_tests_stable_under_cutoff(self):
        progs = [
            ("net", "first.sh"),
            ("net", "second.sh"),
            ("net", "third.sh"),
        ]
        history = {
            runtime_history_key("net", "first.sh"): 3.0,
            runtime_history_key("net", "second.sh"): 9.9,
            runtime_history_key("net", "third.sh"): 1.0,
        }

        ordered = sort_progs_by_runtime_history(progs, history, cutoff_sec=10)

        self.assertEqual(ordered, progs)

    def test_runtime_history_round_trip_uses_versioned_payload(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, "test-runtime.json")
            history = {
                runtime_history_key("net", "a.sh"): 11.5,
                runtime_history_key("net", "b.sh"): 3.0,
            }

            save_runtime_history(path, history)
            loaded = load_runtime_history(path)

            self.assertEqual(loaded, history)

    def test_update_runtime_history_prefers_runtime_time_when_present(self):
        updated = update_runtime_history(
            {},
            [{
                "target": "net",
                "prog": "slow.sh",
                "time": 120.0,
                "runtime_time": 45.0,
            }],
        )

        self.assertEqual(updated, {runtime_history_key("net", "slow.sh"): 45.0})


if __name__ == "__main__":
    unittest.main()
