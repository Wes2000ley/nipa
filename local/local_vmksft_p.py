#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import datetime
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


LOCAL_DIR = Path(__file__).resolve().parent
NIPA_ROOT = LOCAL_DIR.parent
REMOTE_DIR = NIPA_ROOT / "contest" / "remote"
if str(REMOTE_DIR) not in sys.path:
    sys.path.insert(0, str(REMOTE_DIR))

from core import NipaLifetime
from lib import CbArg
from lib import Fetcher, namify
from lib import guess_indicators, parse_nested_tests, result_from_indicators
from lib import wait_loadavg

from local_vm import LocalVM, new_local_vm


def get_prog_list(vm, targets, test_path):
    tmpdir = tempfile.mkdtemp()
    targets = " ".join(targets)
    vm.tree_cmd(['make', '-C', test_path, 'TARGETS=' + targets,
                 'INSTALL_PATH=' + tmpdir, 'install'])

    with open(os.path.join(tmpdir, 'kselftest-list.txt'), "r", encoding='utf-8') as fp:
        targets = fp.readlines()
    vm.tree_cmd("rm -rf " + tmpdir)
    return [(e.split(":")[0].strip(), e.split(":")[1].strip()) for e in targets]


def _live_status_write(path, state):
    if not path:
        return

    tmp_path = path + '.tmp'
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp_path, 'w', encoding='utf-8') as fp:
        json.dump(state, fp, sort_keys=True)
    os.replace(tmp_path, path)


def _live_status_touch(path, state):
    if not path:
        return

    counts = {}
    for test in state.get('tests', []):
        status = test.get('status', 'queued')
        counts[status] = counts.get(status, 0) + 1
    state['counts'] = counts
    state['updated'] = str(datetime.datetime.now(datetime.UTC))
    _live_status_write(path, state)


def _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
               live_status_path=None, live_lock=None, live_state=None):
    test_path = config.get('ksft', 'test_path', fallback='tools/testing/selftests')
    vm = None
    vm_id = -1

    while True:
        try:
            work_item = in_queue.get(block=False)
        except queue.Empty:
            print(f"INFO: thr-{thr_id} has no more work, exiting")
            break

        test_id = work_item['tid']
        prog = work_item['prog']
        target = work_item['target']
        test_name = namify(prog)
        file_name = f"{test_id}-{test_name}"
        is_retry = 'result' in work_item

        if live_status_path and live_lock and live_state:
            with live_lock:
                entry = live_state['tests'][test_id - 1]
                entry['worker'] = thr_id
                entry['attempts'] += 1
                entry['started'] = str(datetime.datetime.now(datetime.UTC))
                entry['status'] = 'retry-running' if is_retry else 'running'
                _live_status_touch(live_status_path, live_state)

        deadline = (hard_stop - datetime.datetime.now(datetime.UTC)).total_seconds()

        if is_retry:
            file_name += '-retry'
        # Don't run retries if we can't finish with 10min to spare
        if is_retry and deadline - work_item['time'] < 10 * 60:
            print(f"INFO: thr-{thr_id} retry skipped == " + prog)
            out_queue.put(work_item)
            continue

        if vm is None:
            vm_id, vm = new_local_vm(results_path, vm_id, config=config, thr=thr_id)

        print(f"INFO: thr-{thr_id} testing == " + prog)
        t1 = datetime.datetime.now()
        vm.cmd(f'make -C {test_path} TARGETS="{target}" TEST_PROGS={prog} TEST_GEN_PROGS="" run_tests')
        try:
            vm.drain_to_prompt(deadline=deadline)
            retcode = vm.bash_prev_retcode()
        except TimeoutError:
            print(f"INFO: thr-{thr_id} test timed out:", prog)
            vm.kill_current_cmd()
            retcode = 1

        t2 = datetime.datetime.now()

        indicators = guess_indicators(vm.log_out)
        result = result_from_indicators(retcode, indicators)

        vm.check_health()

        crashes = None
        if vm.fail_state == 'oops':
            print(f"INFO: thr-{thr_id} test crashed kernel:", prog)
            crashes = vm.extract_crash(results_path + f'/vm-crash-thr{thr_id}-{vm_id}')
            # Extraction will clear/discard false-positives (ignored traces)
            # check VM is still in failed state
            if vm.fail_state:
                result = "fail"

        print(f"INFO: thr-{thr_id} {prog} >> retcode:", retcode, "result:", result, "found", indicators)

        if is_retry:
            outcome = work_item
            outcome['retry'] = result
        else:
            outcome = {'tid': test_id, 'prog': prog, 'target': target,
                       'test': test_name, 'file_name': file_name,
                       'result': result, 'time': (t2 - t1).total_seconds()}
            if crashes:
                outcome['crashes'] = crashes

        if config.getboolean('ksft', 'nested_tests', fallback=False):
            if is_retry:
                prev_results = outcome['results'] if 'results' in outcome else []
            else:
                prev_results = None

            # this will only parse nested tests inside the TAP comments
            nested_tests = parse_nested_tests(vm.log_out, namify, prev_results)
            if nested_tests:
                outcome['results'] = nested_tests

            print(f"INFO: thr-{thr_id} {prog} >> nested tests: {len(nested_tests)}")

        can_retry = not is_retry

        post_check = config.get('ksft', 'post_check', fallback=None)
        if post_check and not vm.fail_state:
            vm.cmd(post_check)
            vm.drain_to_prompt()
            pc = vm.bash_prev_retcode()
            if pc != 0:
                vm.fail_state = "env-check-fail"
                if result == 'pass':
                    result = 'fail'
                    can_retry = False  # Don't waste time, the test is buggy

        if can_retry and result == 'fail':
            if live_status_path and live_lock and live_state:
                with live_lock:
                    entry = live_state['tests'][test_id - 1]
                    entry['result'] = result
                    entry['status'] = 'retry-queued'
                    entry['time'] = outcome.get('time')
                    if 'crashes' in outcome:
                        entry['crashes'] = outcome['crashes']
                    if 'results' in outcome:
                        entry['results'] = outcome['results']
                    _live_status_touch(live_status_path, live_state)
            in_queue.put(outcome)
        else:
            if live_status_path and live_lock and live_state:
                with live_lock:
                    entry = live_state['tests'][test_id - 1]
                    if is_retry:
                        entry['retry'] = result
                    else:
                        entry['result'] = result
                    entry['status'] = result
                    entry['time'] = outcome.get('time')
                    entry['finished'] = str(datetime.datetime.now(datetime.UTC))
                    if 'crashes' in outcome:
                        entry['crashes'] = outcome['crashes']
                    if 'results' in outcome:
                        entry['results'] = outcome['results']
                    _live_status_touch(live_status_path, live_state)
            out_queue.put(outcome)

        vm.dump_log(results_path + '/' + file_name, result=retcode,
                    info={"thr-id": thr_id, "vm-id": vm_id, "time": (t2 - t1).total_seconds(),
                          "found": indicators, "vm_state": vm.fail_state})

        if vm.fail_state:
            print(f"INFO: thr-{thr_id} VM {vm.fail_state}, destroying it")
            vm.stop()
            vm.dump_log(results_path + f'/vm-stop-thr{thr_id}-{vm_id}')
            vm = None

    if vm is not None:
        vm.capture_gcov(results_path + f'/kernel-thr{thr_id}-{vm_id}.lcov')
        vm.stop()
        vm.dump_log(results_path + f'/vm-stop-thr{thr_id}-{vm_id}')
    return


def vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
              live_status_path=None, live_lock=None, live_state=None):
    try:
        _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
                   live_status_path=live_status_path, live_lock=live_lock,
                   live_state=live_state)
    except Exception:
        print(f"ERROR: thr-{thr_id} has crashed")
        raise


def test(binfo, rinfo, cbarg):
    print("Run at", datetime.datetime.now())
    if not hasattr(cbarg, "prev_runtime"):
        cbarg.prev_runtime = dict()
    cbarg.refresh_config()
    config = cbarg.config

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']
    rinfo['link'] = link
    targets = config.get('ksft', 'target').split()
    test_path = config.get('ksft', 'test_path', fallback='tools/testing/selftests')
    grp_name = "selftests-" + namify(targets[0])
    live_status_path = config.get('local', 'live_status_path', fallback=None)
    live_status = None
    live_lock = threading.Lock()

    if config.get('device', 'info_script', fallback=None):
        dev_info = subprocess.run(config.get('device', 'info_script'),
                                  shell=True, stdout=subprocess.PIPE, check=True)
        rinfo['device'] = dev_info.stdout.decode('utf-8').strip()

    if live_status_path:
        live_status = {
            'executor': config.get('executor', 'name'),
            'branch': binfo['branch'],
            'group': grp_name,
            'status': 'building',
            'finished': False,
            'start': str(datetime.datetime.now(datetime.UTC)),
            'targets': targets,
            'run_link': link,
            'results_manifest_url': config.get('www', 'url') + '/' +
                                    config.get('local', 'json_path') + '/results.json',
            'summary_url': config.get('www', 'url') + '/summary.html',
            'tests': [],
            'build': {
                'status': 'running',
                'log_url': link + '/build',
            },
        }
        _live_status_touch(live_status_path, live_status)

    vm = LocalVM(config)

    build_ok = True
    kconfs = []
    for target in targets:
        conf = f"{test_path}/{target}/config"
        if os.path.exists(os.path.join(vm.tree_path, conf)):
            kconfs.append(conf)
    build_ok &= vm.build(kconfs)

    shutil.copy(os.path.join(config.get('local', 'tree_path'), '.config'),
                results_path + '/config')
    vm.tree_cmd("make headers")
    ret = vm.tree_cmd(["make", "-C", test_path,
                       "TARGETS=" + " ".join(targets)])
    build_ok &= ret == 0
    vm.dump_log(results_path + '/build')
    if not build_ok:
        if live_status_path and live_status:
            with live_lock:
                live_status['build']['status'] = 'fail'
                live_status['status'] = 'build-failed'
                live_status['finished'] = True
                _live_status_touch(live_status_path, live_status)
        return [{
            'test': 'build',
            'group': grp_name,
            'result': 'fail',
            'link': link + '/build',
        }]

    progs = get_prog_list(vm, targets, test_path)
    progs.sort(reverse=True, key=lambda prog: cbarg.prev_runtime.get(prog, 0))

    if live_status_path and live_status:
        with live_lock:
            live_status['build']['status'] = 'pass'
            live_status['status'] = 'running'
            live_status['tests'] = []
            for i, prog in enumerate(progs, start=1):
                test_name = namify(prog[1])
                file_name = f"{i}-{test_name}"
                live_status['tests'].append({
                    'tid': i,
                    'target': prog[0],
                    'prog': prog[1],
                    'test': test_name,
                    'group': "selftests-" + namify(prog[0]),
                    'file_name': file_name,
                    'log_url': link + '/' + file_name,
                    'status': 'queued',
                    'attempts': 0,
                })
            _live_status_touch(live_status_path, live_status)

    dl_min = config.getint('executor', 'deadline_minutes', fallback=999999)
    hard_stop = datetime.datetime.fromisoformat(binfo["date"])
    hard_stop += datetime.timedelta(minutes=dl_min)

    in_queue = queue.Queue()
    out_queue = queue.Queue()
    threads = []

    i = 0
    for prog in progs:
        i += 1
        in_queue.put({'tid': i, 'target': prog[0], 'prog': prog[1]})

    # In case we have multiple tests kicking off on the same machine,
    # add optional wait to make sure others have finished building
    load_tgt = config.getfloat("cfg", "wait_loadavg", fallback=None)
    load_ival = config.getfloat("cfg", "wait_loadavg_ival", fallback=30)
    thr_cnt = int(config.get("cfg", "thread_cnt"))
    delay = float(config.get("cfg", "thread_spawn_delay", fallback=0))

    for i in range(thr_cnt):
        # Lower the wait for subsequent VMs
        if i == 1:
            time.sleep(delay)
            load_ival /= 2
        wait_loadavg(load_tgt, check_ival=load_ival)
        print("INFO: starting VM", i)
        threads.append(threading.Thread(target=vm_thread,
                                        args=[config, results_path, i, hard_stop,
                                              in_queue, out_queue,
                                              live_status_path, live_lock,
                                              live_status]))
        threads[i].start()

    for i in range(thr_cnt):
        threads[i].join()

    cases = []
    while not out_queue.empty():
        r = out_queue.get()
        if 'time' in r:
            cbarg.prev_runtime[(r["target"], r["prog"])] = r["time"]
        outcome = {
            'test': r['test'],
            'group': "selftests-" + namify(r['target']),
            'result': r["result"],
            'link': link + '/' + r['file_name']
        }
        for key in ['time', 'retry', 'crashes', 'results']:
            if key in r:
                outcome[key] = r[key]
        cases.append(outcome)
    if not in_queue.empty():
        print("ERROR: in queue is not empty")

    if live_status_path and live_status:
        with live_lock:
            live_status['finished'] = True
            live_status['status'] = 'complete'
            _live_status_touch(live_status_path, live_status)

    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    cfg_paths = ['remote.config', 'vmksft.config', 'vmksft-p.config']
    if len(sys.argv) > 1:
        cfg_paths += sys.argv[1:]

    cbarg = CbArg(cfg_paths)
    config = cbarg.config

    base_dir = config.get('local', 'base_path')

    life = NipaLifetime(config)

    f = Fetcher(test, cbarg,
                name=config.get('executor', 'name'),
                branches_url=config.get('remote', 'branches'),
                results_path=os.path.join(base_dir, config.get('local', 'json_path')),
                url_path=config.get('www', 'url') + '/' + config.get('local', 'json_path'),
                tree_path=config.get('local', 'tree_path'),
                patches_path=config.get('local', 'patches_path', fallback=None),
                life=life,
                first_run=config.get('executor', 'init', fallback="continue"))
    f.run()
    life.exit()


if __name__ == "__main__":
    main()
