#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
LIB_DIR = TESTS_DIR.parent / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_vmksft_p import build_nstat_history_path, build_test_command


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


if __name__ == "__main__":
    unittest.main()
