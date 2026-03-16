#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


LOCAL_DIR = Path(__file__).resolve().parent
if str(LOCAL_DIR) not in sys.path:
    sys.path.insert(0, str(LOCAL_DIR))

from local_vmksft_p import _extract_make_var_tokens, build_test_command


class LocalVmksftPTests(unittest.TestCase):
    def test_extract_make_var_tokens_strips_output_prefix(self):
        make_db = "\n".join([
            "TEST_PROGS := devlink.sh netcons_cmdline.sh",
            "TEST_GEN_PROGS := /tmp/out/epoll_busy_poll /tmp/out/reuseport_bpf",
            "TEST_CUSTOM_PROGS := /tmp/out/custom_prog",
        ])

        self.assertEqual(
            _extract_make_var_tokens(make_db, "TEST_PROGS"),
            ["devlink.sh", "netcons_cmdline.sh"],
        )
        self.assertEqual(
            _extract_make_var_tokens(make_db, "TEST_GEN_PROGS"),
            ["epoll_busy_poll", "reuseport_bpf"],
        )
        self.assertEqual(
            _extract_make_var_tokens(make_db, "TEST_CUSTOM_PROGS"),
            ["custom_prog"],
        )

    def test_build_test_command_resets_other_prog_classes(self):
        cmd = build_test_command("tools/testing/selftests",
                                 "net/packetdrill",
                                 "tcp_rcv_toobig.pkt",
                                 "TEST_PROGS",
                                 nstat_history="/tmp/packetdrill.nstat")

        self.assertIn("NSTAT_HISTORY=/tmp/packetdrill.nstat", cmd)
        self.assertIn("TEST_PROGS=tcp_rcv_toobig.pkt", cmd)
        self.assertIn("TEST_GEN_PROGS=", cmd)
        self.assertIn("TEST_CUSTOM_PROGS=", cmd)
        self.assertIn("TARGETS=net/packetdrill", cmd)

    def test_build_test_command_can_target_generated_prog(self):
        cmd = build_test_command("tools/testing/selftests",
                                 "net",
                                 "epoll_busy_poll",
                                 "TEST_GEN_PROGS")

        self.assertIn("TEST_PROGS=", cmd)
        self.assertIn("TEST_GEN_PROGS=epoll_busy_poll", cmd)
        self.assertIn("TEST_CUSTOM_PROGS=", cmd)


if __name__ == "__main__":
    unittest.main()
