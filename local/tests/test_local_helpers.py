#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import sys
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LIB_DIR = TESTS_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from crash import extract_crash, has_crash  # noqa: E402
from naming import namify  # noqa: E402
from results import guess_indicators, parse_nested_tests, result_from_indicators  # noqa: E402


class LocalHelperTests(unittest.TestCase):
    def test_namify_normalizes_punctuation(self):
        self.assertEqual(namify("net/packetdrill:bar_case.pkt"), "net-packetdrill-bar-case-pkt")
        self.assertEqual(namify(""), "no-name")

    def test_result_helpers_prefer_fail_over_pass_markers(self):
        output = "[PASS]\n[FAIL]\n"
        indicators = guess_indicators(output)

        self.assertEqual(result_from_indicators(0, indicators), "fail")

    def test_parse_nested_tests_parses_ktap_comments(self):
        full_run = (
            "# TAP version 13\n"
            "# ok 1 - Alpha # time=2000ms\n"
            "# not ok 2 - Beta # SKIP disabled locally\n"
        )

        parsed = parse_nested_tests(full_run, namify)

        self.assertEqual(
            parsed,
            [
                {"test": "Alpha", "result": "pass", "time": 2},
                {"test": "Beta", "result": "skip"},
            ],
        )

    def test_extract_crash_fingerprints_memleak_backtrace(self):
        output = (
            "xx__-> echo scan > /sys/kernel/debug/kmemleak && cat /sys/kernel/debug/kmemleak\n"
            "unreferenced object 0xffff888003692380 (size 128):\n"
            "  backtrace (crc 2128895f):\n"
            "    [<ffffffffb2131db6>] kmalloc_trace_noprof+0x236/0x290\n"
            "    [<ffffffffb3dee5e4>] tcp_ao_alloc_info+0x44/0xf0\n"
            "    [<ffffffffb3c2a534>] do_tcp_setsockopt+0xa64/0x2320\n"
            "    [<ffffffffb38e3629>] do_sock_setsockopt+0x149/0x3a0\n"
            "    [<ffffffffb38ee8b4>] __sys_setsockopt+0x104/0x1a0\n"
            "xx__-> \n"
        )

        self.assertTrue(has_crash(output))
        lines, fingerprints = extract_crash(output, "xx__-> ", lambda: None)

        self.assertGreater(len(lines), 5)
        self.assertEqual(
            fingerprints,
            {"kmalloc_trace_noprof:tcp_ao_alloc_info:do_tcp_setsockopt:do_sock_setsockopt:__sys_setsockopt"},
        )

    def test_extract_crash_finalizes_fingerprint_when_log_ends_mid_crash(self):
        output = (
            "[ 1.0] Hardware name: x\n"
            "[ 1.1] Call Trace:\n"
            "[ 1.2]  foo+0x1/0x2\n"
            "[ 1.3]  bar+0x1/0x2\n"
            "[ 1.4]  baz+0x1/0x2\n"
        )

        _lines, fingerprints = extract_crash(output, "xx__-> ", lambda: None)

        self.assertEqual(fingerprints, {"foo:bar:baz"})


if __name__ == "__main__":
    unittest.main()
