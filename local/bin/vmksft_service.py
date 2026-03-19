#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import signal
import subprocess
import sys
import threading
import time

from pathlib import Path

LOCAL_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = LOCAL_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from vmksft_job_lib import ensure_public_site, execute_job, refresh_site_history
from vmksft_http import create_http_server, serve_http_in_thread
from vmksft_service_lib import (
    ensure_layout,
    finish_job,
    load_runtime_config,
    next_queued_job,
    recover_stale_running_jobs,
    set_job_runner_pid,
    write_public_status,
)


SERVICE_POLL_INTERVAL_SEC = 1.0
RUN_HISTORY_REFRESH_INTERVAL_SEC = 5.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Serve and execute long-lived local vmksft jobs.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the long-lived vmksft service loop")

    run_job = subparsers.add_parser("run-job", help="Run one queued vmksft job")
    run_job.add_argument("--job-id", required=True, help="Queued job id to execute")

    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "serve"
    return args


class VmksftService:
    def __init__(self, config):
        self.config = config
        self.stop_evt = threading.Event()
        self.httpd = None
        self.http_thread = None
        self.child = None
        self.current_job_id = None
        self.next_history_refresh_at = 0.0

    def _handle_signal(self, signum, _frame):
        print(f"[vmksft-service] received signal {signum}, shutting down")
        self.stop_evt.set()

    def install_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def start_http(self):
        self.httpd = create_http_server("0.0.0.0", self.config.web_port, self.config.site_root)
        self.http_thread = serve_http_in_thread(self.httpd)
        print(f"[vmksft-service] serving {self.config.site_root} on 0.0.0.0:{self.config.web_port}")

    def shutdown_http(self):
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.http_thread is not None:
            self.http_thread.join(timeout=5)

    def _refresh_history(self):
        try:
            refresh_site_history(self.config)
        except Exception as exc:
            print(f"[vmksft-service] history refresh failed: {exc}")

    def force_refresh_history(self):
        self._refresh_history()
        if self.child is None:
            self.next_history_refresh_at = 0.0
        else:
            self.next_history_refresh_at = time.monotonic() + RUN_HISTORY_REFRESH_INTERVAL_SEC

    def maybe_refresh_history(self):
        if self.child is None:
            return
        now = time.monotonic()
        if now < self.next_history_refresh_at:
            return
        self._refresh_history()
        self.next_history_refresh_at = now + RUN_HISTORY_REFRESH_INTERVAL_SEC

    def build_job_command(self, job_id):
        return [sys.executable, str(Path(__file__).resolve()), "run-job", "--job-id", job_id]

    def launch_next_job(self):
        job = next_queued_job(self.config)
        if job is None:
            return

        command = self.build_job_command(job["job_id"])
        print(
            "[vmksft-service] starting "
            f"{job['job_id']} mode={job['requested_mode']}"
        )
        try:
            self.child = subprocess.Popen(
                command,
                cwd=self.config.script_dir,
                text=True,
            )
        except Exception as exc:
            finish_job(self.config, job["job_id"], 1, f"failed to launch job process: {exc}")
            self.child = None
            self.current_job_id = None
            write_public_status(self.config)
            return

        self.current_job_id = job["job_id"]
        set_job_runner_pid(self.config, job["job_id"], self.child.pid)
        self.next_history_refresh_at = 0.0
        write_public_status(self.config)

    def stop_running_job(self):
        if self.child is None or self.current_job_id is None:
            return

        print(f"[vmksft-service] stopping running job {self.current_job_id}")
        self.child.terminate()
        try:
            rc = self.child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.child.kill()
            rc = self.child.wait(timeout=30)
        finish_job(
            self.config,
            self.current_job_id,
            130 if rc == 0 else rc,
            "service shutdown interrupted the job",
        )
        self.child = None
        self.current_job_id = None
        write_public_status(self.config)

    def poll_running_job(self):
        if self.child is None or self.current_job_id is None:
            return

        rc = self.child.poll()
        if rc is None:
            return

        detail = "job process exited successfully" if rc == 0 else f"job process exited with status {rc}"
        finish_job(self.config, self.current_job_id, rc, detail)
        print(f"[vmksft-service] finished {self.current_job_id} status={rc}")
        self.child = None
        self.current_job_id = None
        write_public_status(self.config)
        self.force_refresh_history()

    def run(self):
        ensure_layout(self.config)
        ensure_public_site(self.config)
        recovered = recover_stale_running_jobs(self.config)
        if recovered:
            print(f"[vmksft-service] recovered stale running jobs: {', '.join(recovered)}")
        self.force_refresh_history()
        write_public_status(self.config)
        self.start_http()
        self.install_signal_handlers()

        try:
            while not self.stop_evt.is_set():
                if self.child is None:
                    self.launch_next_job()
                else:
                    self.poll_running_job()
                    self.maybe_refresh_history()
                self.stop_evt.wait(SERVICE_POLL_INTERVAL_SEC)
        finally:
            self.stop_running_job()
            self.force_refresh_history()
            write_public_status(self.config)
            self.shutdown_http()


def run_job(config, job_id):
    ensure_layout(config)
    ensure_public_site(config)
    return execute_job(config, job_id)


def main(argv=None):
    args = parse_args(argv)
    config = load_runtime_config(__file__)

    if args.command == "run-job":
        return run_job(config, args.job_id)

    service = VmksftService(config)
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
