#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import sys
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LIB_DIR = TESTS_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_vm import RETCODE_MARKER, parse_bash_prev_retcode_output  # noqa: E402


class LocalVMTests(unittest.TestCase):
    def test_parse_bash_prev_retcode_output_accepts_marker_with_shell_noise(self):
        stdout = (
            "printf '__NIPA_PREV_RC__%s\\n' $?\n"
            "[?2004l]3008;start=abcd;cwd=/tmp/work\\"
            f"{RETCODE_MARKER}2\n"
            "]3008;end=abcd;exit=success\\]3008;start=efgh\\[?2004hxx__-> "
        )

        self.assertEqual(parse_bash_prev_retcode_output(stdout), 2)

    def test_parse_bash_prev_retcode_output_keeps_plain_line_fallback(self):
        stdout = "echo $?\n17\nxx__-> "

        self.assertEqual(parse_bash_prev_retcode_output(stdout), 17)


if __name__ == "__main__":
    unittest.main()
