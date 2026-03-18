#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


LOCAL_DIR = Path(__file__).resolve().parent
LOCAL_ROOT = LOCAL_DIR.parent
NIPA_ROOT = LOCAL_ROOT.parent
REMOTE_DIR = NIPA_ROOT / "contest" / "remote"
if str(REMOTE_DIR) not in sys.path:
    sys.path.insert(0, str(REMOTE_DIR))

import lib.vm as upstream_vm
from lib.vm import VM


def decode_and_filter(buf):
    # Strip the full bracketed-paste toggle sequence before decoding so the
    # trailing "h"/"l" byte does not leak into prompt or retcode parsing.
    buf = re.sub(rb'\x1b\[\?2004[hl]', b'', buf)

    buf = buf.decode("utf-8", "ignore")
    buf = re.sub(r'\[\?2004[hl]', '', buf)
    return "".join(
        [x for x in buf if (x in ['\n'] or unicodedata.category(x)[0] != "C")]
    )


upstream_vm.decode_and_filter = decode_and_filter


class LocalVM(VM):
    def _build_state_path(self):
        return os.path.join(self.tree_path, '.nipa-cache', 'vng-build.json')

    def _build_artifacts_ready(self):
        needed = [
            os.path.join(self.tree_path, '.config'),
            os.path.join(self.tree_path, 'arch/x86/boot/bzImage'),
        ]
        return all(os.path.isfile(path) for path in needed)

    def _build_signature(self, configs):
        tree = subprocess.run(['git', 'rev-parse', 'HEAD^{tree}'],
                              cwd=self.tree_path,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL,
                              text=True,
                              check=True)
        return {
            'tree': tree.stdout.strip(),
            'configs': configs,
            'gcov': self.has_gcov,
        }

    def _load_build_state(self):
        path = self._build_state_path()
        if not os.path.isfile(path):
            return None

        with open(path, 'r', encoding='utf-8') as fp:
            return json.load(fp)

    def _store_build_state(self, state):
        path = self._build_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fp:
            json.dump(state, fp, sort_keys=True)

    def _clear_build_state(self):
        path = self._build_state_path()
        if os.path.isfile(path):
            os.unlink(path)

    def build(self, extra_configs, override_configs=None):
        if self.log_out or self.log_err:
            raise Exception("Logs were not flushed before calling build")

        configs = []
        if override_configs is not None:
            configs += override_configs
        elif self.config.get('vm', 'configs', fallback=None):
            configs += self.config.get('vm', 'configs').split(",")
        if extra_configs:
            configs += extra_configs

        gcov = " --configitem GCOV_KERNEL=y" if self.has_gcov else ""
        build_reuse = self.config.getboolean('vm', 'build_reuse', fallback=False)
        build_clean = self.config.get('vm', 'build_clean', fallback='always').strip().lower()
        if build_clean not in ['always', 'never', 'config-change']:
            raise ValueError(f"unsupported build_clean policy: {build_clean}")
        build_state = self._build_signature(configs)
        artifacts_ready = self._build_artifacts_ready()
        cached_state = self._load_build_state() if build_reuse else None

        if build_reuse and artifacts_ready and cached_state == build_state:
            print(f"INFO{self.print_pfx} reusing cached kernel build")
            self.log_out += "INFO: reusing cached kernel build\n"
            return True

        print(f"INFO{self.print_pfx} building kernel")
        if build_clean == 'always':
            # Make sure we rebuild, config and module deps can be stale otherwise
            self.tree_cmd("make mrproper")
        elif build_clean == 'config-change':
            # If the config inputs changed, rebuild from a known-clean tree.
            if (artifacts_ready and cached_state is None) or (
                cached_state is not None and (
                    cached_state.get('configs') != build_state['configs'] or
                    cached_state.get('gcov') != build_state['gcov']
                )
            ):
                self.tree_cmd("make mrproper")

        rc = self.tree_cmd("vng -v -b" + " -f ".join([""] + configs) + gcov)
        if rc != 0:
            self._clear_build_state()
            print(f"INFO{self.print_pfx} kernel build failed")
            return False

        self._store_build_state(build_state)
        return True

    def bash_prev_retcode(self):
        self.cmd("echo $?")
        stdout, stderr = self.drain_to_prompt()
        # Some shells emit bracketed-paste toggles around the echoed status.
        # Accept the last bare integer line instead of assuming a fixed offset.
        for line in reversed(stdout.splitlines()):
            line = re.sub(r'\[\?2004[hl]', '', line).strip()
            if re.fullmatch(r'\d+', line):
                return int(line)
        raise ValueError(f"unable to parse shell return code from stdout: {stdout!r}")


def new_local_vm(results_path, vm_id, thr=None, vm=None, config=None, cwd=None):
    thr_pfx = f"thr{thr}-" if thr is not None else ""
    if vm is None:
        vm = LocalVM(config, vm_name=f"{thr_pfx}{vm_id + 1}")
    # For whatever reason starting sometimes hangs / crashes
    i = 0
    while True:
        try:
            vm.start(cwd=cwd)
            vm_id += 1
            vm.dump_log(results_path + '/vm-start-' + thr_pfx + str(vm_id))
            return vm_id, vm
        except TimeoutError:
            i += 1
            if i > 4:
                raise
            print(f"WARN{vm.print_pfx} VM did not start, retrying {i}/4")
            vm.dump_log(results_path + f'/vm-crashed-{thr_pfx}{vm_id}-{i}')
            vm.stop()
