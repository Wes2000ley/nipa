#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import collections
import os
import threading
import time


def size_to_mib(value):
    value = value.strip().upper()
    if value.endswith("K"):
        return (int(value[:-1]) + 1023) // 1024
    if value.endswith("M"):
        return int(value[:-1])
    if value.endswith("G"):
        return int(value[:-1]) * 1024
    if value.endswith("T"):
        return int(value[:-1]) * 1024 * 1024
    raise ValueError(f"unsupported size value: {value}")


def _read_cpu_counters():
    with open("/proc/stat", "r", encoding="utf-8") as fp:
        fields = fp.readline().split()

    counters = [int(value) for value in fields[1:]]
    total = sum(counters)
    idle = counters[3]
    if len(counters) > 4:
        idle += counters[4]
    return total, idle


def _read_meminfo():
    values = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as fp:
        for line in fp:
            key, value = line.split(":", 1)
            values[key] = int(value.split()[0])

    return {
        "mem_available_mib": values.get("MemAvailable", 0) / 1024,
        "dirty_mib": values.get("Dirty", 0) / 1024,
    }


class HostPressureTracker:
    def __init__(self):
        self._prev_cpu = None

    def sample(self):
        total, idle = _read_cpu_counters()
        cpu_busy_pct = None
        if self._prev_cpu is not None:
            prev_total, prev_idle = self._prev_cpu
            total_delta = total - prev_total
            idle_delta = idle - prev_idle
            if total_delta > 0:
                cpu_busy_pct = max(0.0, min(100.0, 100.0 * (1.0 - (idle_delta / total_delta))))

        self._prev_cpu = (total, idle)
        load1, load5, load15 = os.getloadavg()
        mem = _read_meminfo()
        return {
            "cpu_busy_pct": cpu_busy_pct,
            "mem_available_mib": mem["mem_available_mib"],
            "dirty_mib": mem["dirty_mib"],
            "load1": load1,
            "load5": load5,
            "load15": load15,
        }


class DynamicWorkerScheduler:
    def __init__(self, max_workers, min_workers=0, target_cpu_pct=90.0,
                 sample_period_sec=1.0, history_secs=5.0,
                 up_hysteresis_pct=4.0, down_hysteresis_pct=2.0,
                 min_available_mem_mib=0.0, max_dirty_mib=512.0,
                 vm_idle_shutdown_sec=15.0, vm_start_cooldown_sec=0.5,
                 sample_provider=None, status_cb=None, time_source=None,
                 log=None):
        self.max_workers = max(0, int(max_workers))
        self.min_workers = max(0, min(int(min_workers), self.max_workers))
        self.target_cpu_pct = float(target_cpu_pct)
        self.sample_period_sec = max(0.1, float(sample_period_sec))
        self.history_secs = max(self.sample_period_sec, float(history_secs))
        self.up_hysteresis_pct = max(0.0, float(up_hysteresis_pct))
        self.down_hysteresis_pct = max(0.0, float(down_hysteresis_pct))
        self.min_available_mem_mib = max(0.0, float(min_available_mem_mib))
        self.max_dirty_mib = max(0.0, float(max_dirty_mib))
        self.vm_idle_shutdown_sec = max(0.0, float(vm_idle_shutdown_sec))
        self.vm_start_cooldown_sec = max(0.0, float(vm_start_cooldown_sec))
        self.status_cb = status_cb
        self._time_source = time_source or time.monotonic
        self._log = log
        self._history = collections.deque(
            maxlen=max(1, int(round(self.history_secs / self.sample_period_sec)))
        )
        self._cond = threading.Condition()
        self._stop_evt = threading.Event()
        self._worker_thread = None
        self._slot_holders = set()
        self._desired_workers = 0
        self._last_scale_up_at = self._time_source() - self.vm_start_cooldown_sec
        self._blocked_reason = "warming"
        self._metrics = {
            "cpu_busy_pct": 0.0,
            "mem_available_mib": 0.0,
            "dirty_mib": 0.0,
            "load1": 0.0,
            "load5": 0.0,
            "load15": 0.0,
            "window_samples": 0,
        }
        self._last_logged = None

        if sample_provider is None:
            self._tracker = HostPressureTracker()
            self._sample_provider = self._tracker.sample
        else:
            self._tracker = None
            self._sample_provider = sample_provider

    def start(self):
        self.apply_sample(self._sample_provider())
        with self._cond:
            if self._worker_thread is not None:
                return
            self._worker_thread = threading.Thread(
                target=self._run,
                name="local-host-scheduler",
                daemon=True,
            )
            self._worker_thread.start()

    def stop(self):
        self._stop_evt.set()
        with self._cond:
            self._cond.notify_all()
            worker = self._worker_thread
        if worker is not None:
            worker.join()

    def note_queue_change(self):
        with self._cond:
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            return dict(self._build_snapshot_locked())

    def apply_sample(self, sample):
        callback = None
        snapshot = None
        with self._cond:
            self._history.append({
                "cpu_busy_pct": sample.get("cpu_busy_pct"),
                "mem_available_mib": float(sample.get("mem_available_mib", 0.0)),
                "dirty_mib": float(sample.get("dirty_mib", 0.0)),
                "load1": float(sample.get("load1", 0.0)),
                "load5": float(sample.get("load5", 0.0)),
                "load15": float(sample.get("load15", 0.0)),
            })
            self._recompute_locked()
            snapshot = dict(self._build_snapshot_locked())
            self._cond.notify_all()
            callback = self.status_cb

        if callback:
            callback(snapshot)
        return snapshot

    def wait_for_slot(self, worker_id, work_remaining, has_vm=False, idle_since=None):
        with self._cond:
            while True:
                if self._stop_evt.is_set():
                    return "exit"
                if not work_remaining():
                    return "exit"
                if worker_id in self._slot_holders:
                    return "run"
                if len(self._slot_holders) < self._desired_workers:
                    self._slot_holders.add(worker_id)
                    self._cond.notify_all()
                    return "run"
                if has_vm and idle_since is not None and self.vm_idle_shutdown_sec > 0:
                    idle_for = self._time_source() - idle_since
                    if idle_for >= self.vm_idle_shutdown_sec:
                        return "stop-vm"
                self._cond.wait(timeout=self.sample_period_sec)

    def release_slot(self, worker_id):
        with self._cond:
            self._slot_holders.discard(worker_id)
            self._cond.notify_all()

    def _run(self):
        while not self._stop_evt.wait(self.sample_period_sec):
            try:
                self.apply_sample(self._sample_provider())
            except Exception as exc:
                if self._log:
                    self._log(f"INFO: scheduler sample failed: {exc}")

    def _recompute_locked(self):
        cpu_values = [
            sample["cpu_busy_pct"]
            for sample in self._history
            if sample["cpu_busy_pct"] is not None
        ]
        mem_values = [sample["mem_available_mib"] for sample in self._history]
        dirty_values = [sample["dirty_mib"] for sample in self._history]
        load1_values = [sample["load1"] for sample in self._history]
        load5_values = [sample["load5"] for sample in self._history]
        load15_values = [sample["load15"] for sample in self._history]

        avg_cpu = sum(cpu_values) / len(cpu_values) if cpu_values else 0.0
        avg_mem = sum(mem_values) / len(mem_values) if mem_values else 0.0
        avg_dirty = sum(dirty_values) / len(dirty_values) if dirty_values else 0.0
        avg_load1 = sum(load1_values) / len(load1_values) if load1_values else 0.0
        avg_load5 = sum(load5_values) / len(load5_values) if load5_values else 0.0
        avg_load15 = sum(load15_values) / len(load15_values) if load15_values else 0.0

        self._metrics = {
            "cpu_busy_pct": round(avg_cpu, 1),
            "mem_available_mib": round(avg_mem, 1),
            "dirty_mib": round(avg_dirty, 1),
            "load1": round(avg_load1, 2),
            "load5": round(avg_load5, 2),
            "load15": round(avg_load15, 2),
            "window_samples": len(self._history),
        }

        prev_desired = self._desired_workers
        reason = "target-band"
        now = self._time_source()

        mem_low = avg_mem < self.min_available_mem_mib
        dirty_high = avg_dirty > self.max_dirty_mib
        cpu_high = avg_cpu >= (self.target_cpu_pct + self.down_hysteresis_pct)
        cpu_low = avg_cpu <= (self.target_cpu_pct - self.up_hysteresis_pct)

        if self.max_workers == 0:
            self._desired_workers = 0
            reason = "disabled"
        elif mem_low:
            self._desired_workers = max(self.min_workers, self._desired_workers - 1)
            reason = "mem-pressure"
        elif dirty_high:
            self._desired_workers = max(self.min_workers, self._desired_workers - 1)
            reason = "dirty-memory"
        elif cpu_high:
            self._desired_workers = max(self.min_workers, self._desired_workers - 1)
            reason = "cpu-high"
        elif self._desired_workers < self.max_workers and cpu_low:
            if now - self._last_scale_up_at >= self.vm_start_cooldown_sec:
                self._desired_workers += 1
                self._last_scale_up_at = now
                reason = "cpu-low"
            else:
                reason = "cooldown"
        elif self._desired_workers >= self.max_workers:
            reason = "max-workers"

        if self.max_workers > 0 and self._desired_workers < self.min_workers:
            self._desired_workers = self.min_workers
            if reason in {"target-band", "cooldown"}:
                reason = "min-workers"

        self._blocked_reason = reason
        self._maybe_log_transition_locked(prev_desired)

    def _build_snapshot_locked(self):
        return {
            "cpu_busy_pct": self._metrics["cpu_busy_pct"],
            "mem_available_mib": self._metrics["mem_available_mib"],
            "dirty_mib": self._metrics["dirty_mib"],
            "load1": self._metrics["load1"],
            "load5": self._metrics["load5"],
            "load15": self._metrics["load15"],
            "window_samples": self._metrics["window_samples"],
            "active_workers": len(self._slot_holders),
            "desired_workers": self._desired_workers,
            "max_workers": self.max_workers,
            "min_workers": self.min_workers,
            "target_cpu_pct": self.target_cpu_pct,
            "min_available_mem_mib": self.min_available_mem_mib,
            "max_dirty_mib": self.max_dirty_mib,
            "sample_period_sec": self.sample_period_sec,
            "history_secs": self.history_secs,
            "vm_idle_shutdown_sec": self.vm_idle_shutdown_sec,
            "blocked_reason": self._blocked_reason,
        }

    def _maybe_log_transition_locked(self, prev_desired):
        if not self._log:
            return

        signature = (
            self._desired_workers,
            self._blocked_reason,
            self._metrics["cpu_busy_pct"],
            self._metrics["mem_available_mib"],
            self._metrics["dirty_mib"],
        )
        if signature == self._last_logged and prev_desired == self._desired_workers:
            return

        self._last_logged = signature
        self._log(
            "INFO: scheduler "
            f"desired={self._desired_workers}/{self.max_workers} "
            f"cpu={self._metrics['cpu_busy_pct']:.1f}% "
            f"avail_mem={self._metrics['mem_available_mib']:.1f}MiB "
            f"dirty={self._metrics['dirty_mib']:.1f}MiB "
            f"reason={self._blocked_reason}"
        )
