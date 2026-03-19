#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from host_scheduler import DynamicWorkerScheduler, size_to_mib
from local_vm import LocalVM, new_local_vm
from naming import namify
from results import guess_indicators, parse_nested_tests, result_from_indicators


def build_test_command(test_path, target, prog):
    return f'make -C {test_path} TARGETS="{target}" TEST_PROGS={prog} TEST_GEN_PROGS="" run_tests'


def build_nstat_history_path(thr_id, test_id, prog, is_retry=False):
    retry_suffix = "-retry" if is_retry else ""
    return f"/tmp/nipa-nstat-thr{thr_id}-{test_id}-{namify(prog)}{retry_suffix}"


def get_prog_list(vm, targets, test_path):
    tmpdir = tempfile.mkdtemp()
    targets = " ".join(targets)
    vm.tree_cmd(['make', '-C', test_path, 'TARGETS=' + targets,
                 'INSTALL_PATH=' + tmpdir, 'install'])

    with open(os.path.join(tmpdir, 'kselftest-list.txt'), "r", encoding='utf-8') as fp:
        targets = fp.readlines()
    vm.tree_cmd("rm -rf " + tmpdir)
    return [(e.split(":")[0].strip(), e.split(":")[1].strip()) for e in targets]


def parse_skip_tests(value):
    if not value:
        return set()
    return {item for item in re.split(r'[\s,]+', value.strip()) if item}


def filter_prog_list(progs, skip_tests):
    if not skip_tests:
        return progs, []

    kept = []
    skipped = []
    for target, prog in progs:
        prog_name = prog.strip()
        prog_namified = namify(prog_name)
        target_prog = f"{target}:{prog_name}"
        target_namified = f"{target}:{prog_namified}"
        if (prog_name in skip_tests or prog_namified in skip_tests or
                target_prog in skip_tests or target_namified in skip_tests):
            skipped.append((target, prog_name))
            continue
        kept.append((target, prog_name))
    return kept, skipped


def runtime_history_key(target, prog):
    return f"{target}:{prog.strip()}"


def load_runtime_history(path):
    if not path:
        return {}

    try:
        with open(path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}
    records = payload.get("tests", payload)
    if not isinstance(records, dict):
        return {}

    history = {}
    for key, value in records.items():
        if not isinstance(key, str):
            continue
        try:
            runtime = float(value)
        except (TypeError, ValueError):
            continue
        if runtime < 0:
            continue
        history[key] = runtime
    return history


def save_runtime_history(path, history):
    if not path:
        return

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = path + ".tmp"
    payload = {
        "version": 1,
        "tests": dict(sorted(history.items())),
    }
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def sort_progs_by_runtime_history(progs, history, cutoff_sec):
    threshold = max(0.0, float(cutoff_sec))

    def score(item):
        runtime = history.get(runtime_history_key(item[0], item[1]), 0.0)
        if runtime < threshold:
            return 0.0
        return runtime

    return sorted(progs, key=score, reverse=True)


def update_runtime_history(history, test_runs):
    updated = dict(history)
    for result in test_runs:
        if "target" not in result or "prog" not in result:
            continue
        runtime = result.get("runtime_time", result.get("time"))
        if runtime is None:
            continue
        try:
            updated[runtime_history_key(result["target"], result["prog"])] = float(runtime)
        except (TypeError, ValueError):
            continue
    return updated


def load_run_info(config):
    branch = config.get('run', 'branch', fallback='local-vmksft-net')
    branch_date = config.get('run', 'branch_date', fallback=None)
    if not branch_date:
        branch_date = str(datetime.datetime.now(datetime.UTC))
    return {
        'branch': branch,
        'date': branch_date,
    }


def write_run_results(config, binfo, results, rinfo):
    results_root = os.path.join(config.get('local', 'base_path'), config.get('local', 'json_path'))
    os.makedirs(results_root, exist_ok=True)

    start = rinfo['start']
    end = datetime.datetime.now(datetime.UTC)
    file_name = f"results-{rinfo['run-cookie']}.json"
    entry = {
        'executor': config.get('executor', 'name'),
        'branch': binfo['branch'],
        'start': str(start),
        'end': str(end),
        'results': results,
    }
    if 'link' in rinfo:
        entry['link'] = rinfo['link']
    if 'device' in rinfo:
        entry['device'] = rinfo['device']

    with open(os.path.join(results_root, file_name), "w", encoding="utf-8") as fp:
        json.dump(entry, fp)

    manifest_path = os.path.join(results_root, "results.json")
    url = config.get('www', 'url') + '/' + config.get('local', 'json_path') + '/' + file_name
    with open(manifest_path, "w", encoding="utf-8") as fp:
        json.dump([{
            'url': url,
            'branch': binfo['branch'],
            'executor': config.get('executor', 'name'),
        }], fp)


def load_executor_config(cfg_paths):
    config = configparser.ConfigParser()
    config.read(normalize_cfg_paths(cfg_paths))
    return config


def run_local_once(config):
    binfo = load_run_info(config)
    start = datetime.datetime.now(datetime.UTC)
    rinfo = {
        'run-cookie': str(int(start.timestamp() / 60) % 1000000),
        'start': start,
    }
    results = run_suite(binfo, rinfo, config)
    write_run_results(config, binfo, results, rinfo)
    return results


def normalize_cfg_paths(cfg_paths):
    if isinstance(cfg_paths, (str, os.PathLike)):
        normalized = [str(cfg_paths)]
    else:
        normalized = [str(path) for path in (cfg_paths or [])]

    if not normalized:
        raise ValueError("run_executor requires at least one explicit config path")

    for path in normalized:
        if not Path(path).is_file():
            raise FileNotFoundError(f"executor config not found: {path}")
    return normalized


def run_executor(cfg_paths):
    return run_local_once(load_executor_config(cfg_paths))


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


def _scheduler_status_touch(path, lock, state, snapshot):
    if not path or not lock or not state:
        return

    with lock:
        next_snapshot = dict(snapshot)
        guest_mem_mib = state.get('scheduler', {}).get('guest_mem_mib')
        if guest_mem_mib is not None:
            next_snapshot['guest_mem_mib'] = guest_mem_mib
        state['scheduler'] = next_snapshot
        _live_status_touch(path, state)


def _stop_vm(vm, results_path, thr_id, vm_id, reason, capture_gcov=False):
    if vm is None:
        return None

    if capture_gcov:
        vm.capture_gcov(results_path + f'/kernel-thr{thr_id}-{vm_id}.lcov')
    vm.stop()
    vm.dump_log(results_path + f'/{reason}-thr{thr_id}-{vm_id}')
    return None


def _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
               live_status_path=None, live_lock=None, live_state=None,
               scheduler=None):
    test_path = config.get('ksft', 'test_path', fallback='tools/testing/selftests')
    vm = None
    vm_id = -1
    idle_since = time.monotonic()

    def work_remaining():
        return in_queue.unfinished_tasks > 0

    while True:
        if scheduler is not None:
            action = scheduler.wait_for_slot(thr_id, work_remaining,
                                             has_vm=vm is not None,
                                             idle_since=idle_since)
            if action == "exit":
                print(f"INFO: thr-{thr_id} has no more work, exiting")
                break
            if action == "stop-vm":
                print(f"INFO: thr-{thr_id} idling under pressure, recycling VM")
                vm = _stop_vm(vm, results_path, thr_id, vm_id, "vm-idle-stop",
                              capture_gcov=True)
                idle_since = time.monotonic()
                continue

        try:
            work_item = in_queue.get(block=False)
        except queue.Empty:
            if scheduler is not None:
                scheduler.release_slot(thr_id)
            if not work_remaining():
                print(f"INFO: thr-{thr_id} has no more work, exiting")
                break
            time.sleep(0.2)
            continue

        test_id = work_item['tid']
        prog = work_item['prog']
        target = work_item['target']
        test_name = namify(prog)
        file_name = f"{test_id}-{test_name}"
        is_retry = 'result' in work_item
        idle_since = None

        try:
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
            cmd = build_test_command(test_path, target, prog)
            nstat_history = build_nstat_history_path(thr_id, test_id, prog, is_retry)
            vm.cmd(f'NSTAT_HISTORY={nstat_history} {cmd}')
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

            print(f"INFO: thr-{thr_id} {prog} >> retcode:", retcode,
                  "result:", result, "found", indicators)

            if is_retry:
                outcome = work_item
                outcome['retry'] = result
                outcome['runtime_time'] = (t2 - t1).total_seconds()
            else:
                outcome = {'tid': test_id, 'prog': prog, 'target': target,
                           'test': test_name, 'file_name': file_name,
                           'result': result,
                           'time': (t2 - t1).total_seconds(),
                           'runtime_time': (t2 - t1).total_seconds()}
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

            if is_retry:
                outcome['retry'] = result
            else:
                outcome['result'] = result

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
                vm = _stop_vm(vm, results_path, thr_id, vm_id, "vm-stop")
        finally:
            in_queue.task_done()
            if scheduler is not None:
                scheduler.release_slot(thr_id)
                scheduler.note_queue_change()
            idle_since = time.monotonic()

    if vm is not None:
        vm = _stop_vm(vm, results_path, thr_id, vm_id, "vm-stop", capture_gcov=True)
    return


def vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
              live_status_path=None, live_lock=None, live_state=None,
              scheduler=None, failure_queue=None):
    try:
        _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue,
                   live_status_path=live_status_path, live_lock=live_lock,
                   live_state=live_state, scheduler=scheduler)
    except Exception:
        print(f"ERROR: thr-{thr_id} has crashed")
        if failure_queue is not None:
            failure_queue.put({
                "thread": thr_id,
                "traceback": traceback.format_exc(),
            })
        raise


def run_suite(binfo, rinfo, config):
    print("Run at", datetime.datetime.now())

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

    config_copy_src = os.path.join(config.get('local', 'tree_path'), '.config')
    if os.path.exists(config_copy_src):
        shutil.copy(config_copy_src, results_path + '/config')
    else:
        build_ok = False

    headers_ret = vm.tree_cmd("make headers")
    build_ok &= headers_ret == 0
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
    skip_tests = parse_skip_tests(config.get('ksft', 'skip_tests', fallback=''))
    progs, skipped_progs = filter_prog_list(progs, skip_tests)
    if skipped_progs:
        skipped_names = ", ".join(f"{target}:{prog}" for target, prog in skipped_progs)
        print(f"INFO: skipping {len(skipped_progs)} configured test(s): {skipped_names}")
    runtime_history_path = config.get('local', 'runtime_history_path', fallback='')
    runtime_history_cutoff_sec = config.getfloat('cfg', 'runtime_history_cutoff_sec', fallback=10)
    runtime_history = load_runtime_history(runtime_history_path)
    progs = sort_progs_by_runtime_history(progs, runtime_history, runtime_history_cutoff_sec)

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
    failure_queue = queue.Queue()
    threads = []

    i = 0
    for prog in progs:
        i += 1
        in_queue.put({
            'tid': i,
            'target': prog[0],
            'prog': prog[1],
        })

    thr_cnt = int(config.get("cfg", "thread_cnt"))
    delay = float(config.get("cfg", "thread_spawn_delay", fallback=0))
    target_cpu_pct = config.getfloat("cfg", "scheduler_target_cpu_pct", fallback=90)
    sample_period_sec = config.getfloat("cfg", "scheduler_sample_period_sec", fallback=1)
    history_secs = config.getfloat("cfg", "scheduler_history_sec", fallback=5)
    up_hysteresis_pct = config.getfloat("cfg", "scheduler_up_hysteresis_pct", fallback=4)
    down_hysteresis_pct = config.getfloat("cfg", "scheduler_down_hysteresis_pct", fallback=2)
    min_available_mem_mib = config.getfloat("cfg", "scheduler_min_available_mem_mib", fallback=0)
    max_dirty_mib = config.getfloat("cfg", "scheduler_max_dirty_mib", fallback=512)
    vm_idle_shutdown_sec = config.getfloat("cfg", "scheduler_vm_idle_shutdown_sec", fallback=15)

    scheduler = DynamicWorkerScheduler(
        max_workers=thr_cnt,
        min_workers=0,
        target_cpu_pct=target_cpu_pct,
        sample_period_sec=sample_period_sec,
        history_secs=history_secs,
        up_hysteresis_pct=up_hysteresis_pct,
        down_hysteresis_pct=down_hysteresis_pct,
        min_available_mem_mib=min_available_mem_mib,
        max_dirty_mib=max_dirty_mib,
        vm_idle_shutdown_sec=vm_idle_shutdown_sec,
        vm_start_cooldown_sec=delay,
        status_cb=lambda snapshot: _scheduler_status_touch(
            live_status_path, live_lock, live_status, snapshot
        ),
        log=print,
    )
    scheduler.start()

    if live_status_path and live_status:
        with live_lock:
            live_status['scheduler'] = scheduler.snapshot()
            live_status['scheduler']['guest_mem_mib'] = size_to_mib(config.get('vm', 'mem'))
            _live_status_touch(live_status_path, live_status)

    try:
        for i in range(thr_cnt):
            if i == 1:
                time.sleep(delay)
            print("INFO: starting worker", i)
            threads.append(threading.Thread(target=vm_thread,
                                            args=[config, results_path, i, hard_stop,
                                                  in_queue, out_queue,
                                                  live_status_path, live_lock,
                                                  live_status, scheduler,
                                                  failure_queue]))
            threads[i].start()

        for i in range(thr_cnt):
            threads[i].join()
    finally:
        scheduler.stop()

    cases = []
    while not out_queue.empty():
        r = out_queue.get()
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
        runtime_history = update_runtime_history(runtime_history, [r])
    failures = []
    while not failure_queue.empty():
        failures.append(failure_queue.get())

    if failures:
        print(f"ERROR: {len(failures)} worker thread(s) crashed")
        for failure in failures:
            print(failure["traceback"], end="")

    queue_not_empty = not in_queue.empty()
    if queue_not_empty:
        print("ERROR: in queue is not empty")

    if live_status_path and live_status:
        with live_lock:
            live_status['scheduler'] = scheduler.snapshot()
            live_status['finished'] = True
            if failures or queue_not_empty:
                live_status['status'] = 'failed'
                live_status['failure'] = {
                    'worker_failures': len(failures),
                    'queue_not_empty': queue_not_empty,
                }
            else:
                live_status['status'] = 'complete'
            _live_status_touch(live_status_path, live_status)

    if failures:
        raise RuntimeError(f"{len(failures)} worker thread(s) crashed during execution")
    if queue_not_empty:
        raise RuntimeError("worker queue was not drained before executor exit")

    save_runtime_history(runtime_history_path, runtime_history)
    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    if len(sys.argv) <= 1:
        raise SystemExit("usage: local_vmksft_p.py CONFIG_PATH [CONFIG_PATH ...]")
    run_executor(sys.argv[1:])


if __name__ == "__main__":
    main()
