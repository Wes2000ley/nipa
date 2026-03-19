#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import fcntl
import os
import psutil
import re
import select
import shutil
import subprocess
import time
import unicodedata

from crash import extract_crash, has_crash


def decode_and_filter(buf):
    buf = re.sub(rb"\x1b\[\?2004[hl]", b"", buf)
    buf = buf.decode("utf-8", "ignore")
    buf = re.sub(r"\[\?2004[hl]", "", buf)
    return "".join(x for x in buf if x == "\n" or unicodedata.category(x)[0] != "C")


class VMBase:
    def __init__(self, config, vm_name=""):
        self.fail_state = ""
        self.p = None
        self.procs = []
        self.config = config
        self.vm_name = vm_name
        self.print_pfx = (": " + vm_name) if vm_name else ":"
        self.tree_path = config.get("local", "tree_path")

        self.cfg_boot_to = int(config.get("vm", "boot_timeout"))

        self.has_kmemleak = None
        self.has_gcov = self.config.getboolean("vm", "gcov", fallback=False)
        self.log_out = ""
        self.log_err = ""

    def tree_popen(self, cmd):
        return subprocess.Popen(
            cmd,
            cwd=self.tree_path,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def tree_cmd(self, cmd):
        if isinstance(cmd, str):
            cmd = cmd.split()
        self.log_out += "> TREE CMD: " + " ".join(cmd) + "\n"
        proc = self.tree_popen(cmd)
        stdout, stderr = proc.communicate()
        self.log_out += stdout.decode("utf-8", "ignore")
        self.log_err += stderr.decode("utf-8", "ignore")
        proc.stdout.close()
        proc.stderr.close()
        return proc.returncode

    def build(self, extra_configs, override_configs=None):
        if self.log_out or self.log_err:
            raise Exception("Logs were not flushed before calling build")

        configs = []
        if override_configs is not None:
            configs += override_configs
        elif self.config.get("vm", "configs", fallback=None):
            configs += self.config.get("vm", "configs").split(",")
        if extra_configs:
            configs += extra_configs

        gcov = " --configitem GCOV_KERNEL=y" if self.has_gcov else ""

        print(f"INFO{self.print_pfx} building kernel")
        self.tree_cmd("make mrproper")

        rc = self.tree_cmd("vng -v -b" + " -f ".join([""] + configs) + gcov)
        if rc != 0:
            print(f"INFO{self.print_pfx} kernel build failed")
            return False

        return True

    def _record_guest_env(self):
        self.cmd("env")
        self.drain_to_prompt()

    def start(self, cwd=None):
        cmd = "vng -v -r arch/x86/boot/bzImage --user root".split(" ")
        if cwd:
            cmd += ["--cwd", cwd]

        name = self.config.get("executor", "name", fallback="virtme-ng")
        cmd += ["--name", name + ",debug-threads=on"]

        opts = self.config.get("vm", "virtme_opt", fallback="")
        cmd += opts.split(",") if opts else []

        opts = self.config.get("vm", "qemu_opt", fallback="")
        cmd += ["-o", " " + opts] if opts else []

        cpus = self.config.get("vm", "cpus", fallback="")
        if cpus:
            cmd += ["--cpus", cpus]
        mem = self.config.get("vm", "mem", fallback="")
        if mem:
            cmd += ["--memory", mem]

        print(f"INFO{self.print_pfx} VM starting:", " ".join(cmd))
        self.log_out += "# " + " ".join(cmd) + "\n"
        self.p = self.tree_popen(cmd)

        for pipe in [self.p.stdout, self.p.stderr]:
            flags = fcntl.fcntl(pipe, fcntl.F_GETFL)
            fcntl.fcntl(pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        init_prompt = self.config.get("vm", "init_prompt")
        if init_prompt[-1] != " ":
            init_prompt += " "
        print(f"INFO{self.print_pfx} expecting prompt: '{init_prompt}'")
        try:
            self.drain_to_prompt(prompt=init_prompt, dump_after=self.cfg_boot_to)
        finally:
            proc = psutil.Process(self.p.pid)
            self.procs = proc.children(recursive=True) + [proc]

        print(f"INFO{self.print_pfx} reached initial prompt")
        self.cmd("PS1='xx__-> '")
        self.drain_to_prompt()

        off = len(self.log_out)
        self.cmd("ls /sys/kernel/debug/")
        self.drain_to_prompt()
        self.has_kmemleak = "kmemleak" in self.log_out[off:]
        self.has_gcov = self.has_gcov and "gcov" in self.log_out[off:]

        self._record_guest_env()

    def stop(self):
        if self.p is None:
            return

        if self.p.poll() is None:
            try:
                self.cmd("exit")
            except BrokenPipeError:
                pass
        try:
            stdout, stderr = self.p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            print(
                f"WARN{self.print_pfx} process did not exit, sending a KILL to",
                self.p.pid,
                self.procs,
            )
            for proc in self.procs:
                try:
                    proc.kill()
                except psutil.NoSuchProcess:
                    pass
            stdout, stderr = self.p.communicate(timeout=2)

        self.p.stdout.close()
        self.p.stderr.close()
        stdout = stdout.decode("utf-8", "ignore")
        stderr = stderr.decode("utf-8", "ignore")

        print(f"INFO{self.print_pfx} VM stopped")
        self.log_out += stdout
        self.log_err += stderr

    def cmd(self, command):
        buf = command.encode("utf-8")
        if buf[-1] != ord("\n"):
            buf += b"\n"
        self.p.stdin.write(buf)
        self.p.stdin.flush()

    def ctrl_c(self):
        self.log_out += "\nCtrl-C stdout\n"
        self.log_err += "\nCtrl-C stderr\n"
        self.p.stdin.write(b"\x03")
        self.p.stdin.flush()

    def kill_current_cmd(self):
        try:
            self.ctrl_c()
            self.ctrl_c()
            self.drain_to_prompt(dump_after=12)
        except TimeoutError:
            print(f"WARN{self.print_pfx} failed to interrupt process")

    def _read_pipe_nonblock(self, pipe):
        read_some = False
        output = ""
        try:
            buf = os.read(pipe.fileno(), 1024)
            if not buf:
                return read_some, output
            read_some = True
            output = decode_and_filter(buf)
            if has_crash(output):
                self.fail_state = "oops"
        except BlockingIOError:
            pass
        return read_some, output

    def drain_to_prompt(self, prompt="xx__-> ", dump_after=None, deadline=None):
        effective_dump_after = dump_after
        if dump_after is None:
            dump_after = self.config.getint("vm", "default_timeout")
        hard_stop = self.config.getint("vm", "hard_timeout", fallback=(1 << 63))
        if deadline is not None:
            hard_stop = max(0, min(deadline, hard_stop))

        waited = 0
        total_wait = 0
        stdout = ""
        stderr = ""
        prompt_seen = False
        last_read = time.monotonic()
        while True:
            readable, _, _ = select.select([self.p.stdout, self.p.stderr], [], [], 0.2)

            read_some, out = self._read_pipe_nonblock(self.p.stdout)
            self.log_out += out
            stdout += out
            read_some_err, err = self._read_pipe_nonblock(self.p.stderr)
            read_some |= read_some_err
            self.log_err += err
            stderr += err

            now = time.monotonic()
            elapsed = now - last_read
            last_read = now
            total_wait += elapsed

            if read_some and stdout.endswith(prompt):
                prompt_seen = True
            elif read_some:
                if self.fail_state == "oops" and effective_dump_after is None and dump_after > 300:
                    dump_after = 300
                    self.log_out += "\nDETECTED CRASH, lowering timeout\n"

                if prompt in out:
                    self.cmd("\n")
                    time.sleep(0.25)
                waited = 0
            elif prompt_seen and not read_some and not readable:
                break
            else:
                waited += elapsed

            if total_wait > hard_stop:
                self.log_err += f"\nHARD STOP ({hard_stop})\n"
                waited = 1 << 63
            if waited > dump_after:
                print(
                    f"WARN{self.print_pfx} TIMEOUT retcode:",
                    self.p.returncode,
                    "waited:",
                    waited,
                    "total:",
                    total_wait,
                )
                self.log_out += "\nWAIT TIMEOUT stdout\n"
                self.log_err += "\nWAIT TIMEOUT stderr\n"
                if not self.fail_state:
                    self.fail_state = "timeout"
                raise TimeoutError(stderr, stdout)

        if self.fail_state == "timeout":
            self.fail_state = ""

        return stdout, stderr

    def dump_log(self, dir_path, result=None, info=None):
        os.makedirs(dir_path)

        if self.log_out:
            with open(os.path.join(dir_path, "stdout"), "w", encoding="utf-8") as fp:
                fp.write(self.log_out)
        if self.log_err:
            with open(os.path.join(dir_path, "stderr"), "w", encoding="utf-8") as fp:
                fp.write(self.log_err)
        if result is not None:
            with open(os.path.join(dir_path, "result"), "w", encoding="utf-8") as fp:
                fp.write(repr(result))
        if info is not None:
            strinfo = ""
            for key, value in info.items():
                strinfo += f"{key}:\t{value}\n"
            with open(os.path.join(dir_path, "info"), "w", encoding="utf-8") as fp:
                fp.write(strinfo)

        self.log_out = ""
        self.log_err = ""

    def extract_crash(self, out_path):
        crash_lines, finger_prints = extract_crash(self.log_out + self.log_err, "xx__-> ", lambda: None)
        if not crash_lines:
            print(f"WARN{self.print_pfx} extract_crash found no crashes")
            return ["crash-extract-fail"]

        proc = self.tree_popen("./scripts/decode_stacktrace.sh vmlinux auto ./".split())
        stdout, stderr = proc.communicate("\n".join(crash_lines).encode("utf-8"))
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()
        decoded = stdout.decode("utf-8", "ignore")

        with open(out_path, "a", encoding="utf-8") as fp:
            fp.write("======================================\n")
            fp.write(decoded)
            fp.write("\n\nFinger prints:\n" + "\n".join(finger_prints))

        return list(finger_prints)

    def check_health(self):
        if self.fail_state:
            return
        if self.has_kmemleak:
            self.cmd("echo scan > /sys/kernel/debug/kmemleak")
            self.drain_to_prompt()
            time.sleep(5)
            self.cmd("echo scan > /sys/kernel/debug/kmemleak && cat /sys/kernel/debug/kmemleak")
            self.drain_to_prompt()

    def capture_gcov(self, dest):
        if not self.has_gcov:
            return

        lcov = "kernel.lcov"
        self.cmd(
            "lcov --capture --keep-going --rc geninfo_unexecuted_blocks=1 "
            "--function-coverage --branch-coverage -j $(nproc) -o " + lcov
        )
        self.drain_to_prompt()

        lcov = os.path.join(self.tree_path, lcov)
        if os.path.isfile(lcov):
            shutil.copy(lcov, dest)

    def bash_prev_retcode(self):
        self.cmd("echo $?")
        stdout, stderr = self.drain_to_prompt()
        return int(stdout.split("\n")[1])
