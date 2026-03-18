#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import signal
import subprocess
import sys
import threading

from pathlib import Path

LOCAL_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = LOCAL_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from vmksft_http import create_http_server, serve_http_in_thread
from vmksft_service_lib import (
    build_runner_command,
    ensure_layout,
    find_free_tcp_port,
    finish_job,
    load_runtime_config,
    next_queued_job,
    recover_stale_running_jobs,
    set_job_runner_pid,
    write_public_status,
)


class VmksftService:
    def __init__(self, config):
        self.config = config
        self.stop_evt = threading.Event()
        self.httpd = None
        self.http_thread = None
        self.child = None
        self.current_job_id = None

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

    def launch_next_job(self):
        job = next_queued_job(self.config)
        if job is None:
            return

        internal_http_port = find_free_tcp_port("127.0.0.1")
        command = build_runner_command(self.config, job, internal_http_port)
        print(
            "[vmksft-service] starting "
            f"{job['job_id']} mode={job['requested_mode']} internal_http_port={internal_http_port}"
        )
        try:
            self.child = subprocess.Popen(
                command,
                cwd=self.config.script_dir,
                text=True,
            )
        except Exception as exc:
            finish_job(self.config, job["job_id"], 1, f"failed to launch runner: {exc}")
            self.child = None
            self.current_job_id = None
            write_public_status(self.config)
            return

        self.current_job_id = job["job_id"]
        set_job_runner_pid(self.config, job["job_id"], self.child.pid)
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
        finish_job(self.config, self.current_job_id, 130 if rc == 0 else rc, "service shutdown interrupted the job")
        self.child = None
        self.current_job_id = None
        write_public_status(self.config)

    def poll_running_job(self):
        if self.child is None or self.current_job_id is None:
            return

        rc = self.child.poll()
        if rc is None:
            return

        detail = "runner exited successfully" if rc == 0 else f"runner exited with status {rc}"
        finish_job(self.config, self.current_job_id, rc, detail)
        print(f"[vmksft-service] finished {self.current_job_id} status={rc}")
        self.child = None
        self.current_job_id = None
        write_public_status(self.config)

    def run(self):
        ensure_layout(self.config)
        recovered = recover_stale_running_jobs(self.config)
        if recovered:
            print(f"[vmksft-service] recovered stale running jobs: {', '.join(recovered)}")
        write_public_status(self.config)
        self.start_http()
        self.install_signal_handlers()

        try:
            while not self.stop_evt.is_set():
                if self.child is None:
                    self.launch_next_job()
                else:
                    self.poll_running_job()
                write_public_status(self.config)
                self.stop_evt.wait(1.0)
        finally:
            self.stop_running_job()
            write_public_status(self.config)
            self.shutdown_http()


def main():
    config = load_runtime_config(__file__)
    service = VmksftService(config)
    service.run()


if __name__ == "__main__":
    main()
