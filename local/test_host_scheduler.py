#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(__file__))

from host_scheduler import DynamicWorkerScheduler


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def sample(cpu_busy_pct, mem_available_mib=32768, dirty_mib=16):
    return {
        "cpu_busy_pct": cpu_busy_pct,
        "mem_available_mib": mem_available_mib,
        "dirty_mib": dirty_mib,
        "load1": 0.0,
        "load5": 0.0,
        "load15": 0.0,
    }


class HostSchedulerTest(unittest.TestCase):
    def test_history_window_tracks_last_five_samples(self):
        clock = FakeClock()
        scheduler = DynamicWorkerScheduler(
            max_workers=4,
            sample_period_sec=1,
            history_secs=5,
            vm_start_cooldown_sec=0,
            time_source=clock,
        )

        for cpu in [10, 20, 30, 40, 50]:
            scheduler.apply_sample(sample(cpu))
            clock.advance(1)

        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["window_samples"], 5)
        self.assertEqual(snapshot["cpu_busy_pct"], 30.0)

        scheduler.apply_sample(sample(60))
        snapshot = scheduler.snapshot()
        self.assertEqual(snapshot["window_samples"], 5)
        self.assertEqual(snapshot["cpu_busy_pct"], 40.0)

    def test_scheduler_scales_up_and_down(self):
        clock = FakeClock()
        scheduler = DynamicWorkerScheduler(
            max_workers=3,
            target_cpu_pct=90,
            sample_period_sec=1,
            history_secs=1,
            vm_start_cooldown_sec=0,
            time_source=clock,
        )

        for expected in [1, 2, 3]:
            scheduler.apply_sample(sample(20))
            self.assertEqual(scheduler.snapshot()["desired_workers"], expected)
            clock.advance(1)

        for expected in [2, 1, 0]:
            scheduler.apply_sample(sample(97))
            self.assertEqual(scheduler.snapshot()["desired_workers"], expected)
            clock.advance(1)

    def test_idle_vm_gets_reclaimed_when_pressure_blocks_new_work(self):
        clock = FakeClock()
        scheduler = DynamicWorkerScheduler(
            max_workers=1,
            sample_period_sec=1,
            history_secs=1,
            vm_start_cooldown_sec=0,
            vm_idle_shutdown_sec=5,
            time_source=clock,
        )

        scheduler.apply_sample(sample(20))
        self.assertEqual(scheduler.wait_for_slot(0, lambda: True), "run")

        clock.advance(1)
        scheduler.apply_sample(sample(97))
        scheduler.release_slot(0)

        clock.advance(5)
        action = scheduler.wait_for_slot(0, lambda: True, has_vm=True, idle_since=0)
        self.assertEqual(action, "stop-vm")

    def test_wait_for_slot_exits_when_work_is_done(self):
        scheduler = DynamicWorkerScheduler(max_workers=1, sample_period_sec=1, history_secs=1)
        scheduler.apply_sample(sample(20))
        self.assertEqual(scheduler.wait_for_slot(0, lambda: False), "exit")


if __name__ == "__main__":
    unittest.main()
