#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import contextlib
import dataclasses
import json
import os
import shutil
import subprocess
import traceback

from pathlib import Path

from vmksft_service_lib import (
    DEFAULT_BUILD_CLEAN,
    DEFAULT_CPUS,
    DEFAULT_INIT_PROMPT,
    DEFAULT_MEMORY,
    DEFAULT_TARGETS,
    DEFAULT_THREADS,
    _maybe_clone_local,
    load_job_record,
    utc_now,
)


EXECUTOR_NAME = "vmksft-net-local"
DEFAULT_BRANCH_NAME = "local-vmksft-net"
DEFAULT_RESERVED_MEM_GB = 8
DEFAULT_VM_MEM_GB = 2
DEFAULT_THREAD_SPAWN_DELAY = 0.5
DEFAULT_TARGET_CPU_UTIL_PCT = 70
DEFAULT_SCHEDULER_HISTORY_SEC = 5
DEFAULT_SCHEDULER_SAMPLE_PERIOD_SEC = 1
DEFAULT_SCHEDULER_UP_HYSTERESIS_PCT = 4
DEFAULT_SCHEDULER_DOWN_HYSTERESIS_PCT = 2
DEFAULT_VM_IDLE_SHUTDOWN_SEC = 15
DEFAULT_RUNTIME_HISTORY_CUTOFF_SEC = 10

UI_ASSETS = (
    "favicon-contest.png",
    "favicon-status.png",
    "favicon-stats.png",
    "favicon-flakes.png",
    "favicon-nic.png",
)


@dataclasses.dataclass(frozen=True)
class RunLayout:
    run_id: str
    run_dir: Path
    web_root: Path
    executor_root: Path
    config_path: Path
    summary_json: Path
    summary_html: Path
    manifest_path: Path
    live_status_json: Path
    run_meta_json: Path
    executor_log_path: Path
    run_public_prefix: str


def _run(cmd, cwd=None, capture_output=False):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=capture_output,
        check=True,
    )


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_redirect_page(path, target, title):
    write_text(
        path,
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url={target}">
  <title>{title}</title>
</head>
<body>
  <p>Redirecting to <a href="{target}">{target}</a>.</p>
</body>
</html>
""",
    )


def write_infra_failure_page(path, reason):
    write_text(
        path,
        f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="shortcut icon" href="/favicon-status.png" type="image/png">
  <title>{EXECUTOR_NAME} infrastructure failure</title>
</head>
<body>
  <p>The executor did not produce a readable final summary page.</p>
  <p>Reason: {reason}</p>
  <p>Open <a href="../index.html">the run view</a> or <a href="../executor.log">executor.log</a>.</p>
</body>
</html>
""",
    )


def create_relative_symlink(link_path, target):
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink() or link_path.exists():
        if link_path.is_dir() and not link_path.is_symlink():
            shutil.rmtree(link_path)
        else:
            link_path.unlink()
    rel_target = os.path.relpath(target, start=link_path.parent)
    link_path.symlink_to(rel_target)


def ensure_public_site(config):
    site_root = config.site_root
    assets_root = site_root / "assets"
    web_root = config.script_dir / "web"

    assets_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(web_root / "nipa.css", assets_root / "nipa.css")
    shutil.copy2(web_root / "nipa.js", assets_root / "nipa.js")
    shutil.copy2(web_root / "contest.js", assets_root / "contest.js")
    for name in UI_ASSETS:
        shutil.copy2(web_root / name, site_root / name)

    shutil.copy2(web_root / "contest.html", site_root / "contest.html")
    write_redirect_page(site_root / "index.html", "./contest.html", f"{EXECUTOR_NAME} result log")
    (site_root / "contest").mkdir(parents=True, exist_ok=True)
    (site_root / "runs").mkdir(parents=True, exist_ok=True)


def generate_run_id():
    return f"{utc_now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"


def job_targets(job_record):
    return (job_record.get("targets") or DEFAULT_TARGETS).strip() or DEFAULT_TARGETS


def published_branch_name(mode, run_id):
    return f"{DEFAULT_BRANCH_NAME}-{mode}-{run_id}"


def create_run_layout(config, run_id):
    run_dir = config.runs_root / run_id
    web_root = run_dir / "www"
    executor_root = web_root / EXECUTOR_NAME
    run_public_prefix = f"/runs/{run_id}"
    return RunLayout(
        run_id=run_id,
        run_dir=run_dir,
        web_root=web_root,
        executor_root=executor_root,
        config_path=run_dir / f"{EXECUTOR_NAME}.ini",
        summary_json=executor_root / "summary.json",
        summary_html=executor_root / "summary.html",
        manifest_path=executor_root / "jsons" / "results.json",
        live_status_json=executor_root / "live-status.json",
        run_meta_json=web_root / "run-meta.json",
        executor_log_path=run_dir / "executor.log",
        run_public_prefix=run_public_prefix,
    )


def prepare_run_layout(config, layout):
    ensure_public_site(config)
    (layout.executor_root / "jsons").mkdir(parents=True, exist_ok=True)
    (layout.executor_root / "results").mkdir(parents=True, exist_ok=True)

    create_relative_symlink(config.harness_state_dir / "latest", layout.run_dir)
    create_relative_symlink(config.site_root / "latest", config.site_root / "runs" / layout.run_id)
    create_relative_symlink(config.site_root / "runs" / layout.run_id, layout.web_root)

    create_relative_symlink(layout.web_root / "executor.log", layout.run_dir / "executor.log")
    create_relative_symlink(layout.web_root / layout.config_path.name, layout.config_path)


def host_mem_kb():
    with open("/proc/meminfo", "r", encoding="utf-8") as fp:
        for line in fp:
            if line.startswith("MemTotal:"):
                return int(line.split()[1])
    raise RuntimeError("MemTotal not found in /proc/meminfo")


def memory_to_mib(value):
    upper = value.strip().upper()
    if upper.endswith("K"):
        return (int(upper[:-1]) + 1023) // 1024
    if upper.endswith("M"):
        return int(upper[:-1])
    if upper.endswith("G"):
        return int(upper[:-1]) * 1024
    if upper.endswith("T"):
        return int(upper[:-1]) * 1024 * 1024
    raise ValueError(f"unsupported memory size: {value}")


def resolve_guest_memory(value):
    if value == DEFAULT_MEMORY:
        value = f"{DEFAULT_VM_MEM_GB}G"
    memory_to_mib(value)
    return value


def resolve_guest_cpus(value):
    if value == DEFAULT_CPUS:
        return "2"
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"invalid cpu count: {value}")
    return str(parsed)


def resolve_threads(value, host_mem_mib, guest_mem_mib):
    if value != DEFAULT_THREADS:
        parsed = int(value)
        if parsed < 0:
            raise ValueError(f"invalid thread count: {value}")
        return str(parsed)

    reserved_mem_mib = DEFAULT_RESERVED_MEM_GB * 1024
    mem_limited = (host_mem_mib - reserved_mem_mib) // guest_mem_mib
    return str(max(1, mem_limited))


def resolve_scheduler_limits(host_mem_mib, guest_mem_mib):
    reserved_mem_mib = DEFAULT_RESERVED_MEM_GB * 1024
    if guest_mem_mib < reserved_mem_mib:
        min_available_mem_mib = guest_mem_mib
    else:
        min_available_mem_mib = reserved_mem_mib
    min_available_mem_mib = max(1024, min_available_mem_mib)
    min_available_mem_mib = min(min_available_mem_mib, host_mem_mib // 2)
    min_available_mem_mib = max(256, min_available_mem_mib)

    max_dirty_mib = guest_mem_mib // 2
    max_dirty_mib = max(256, max_dirty_mib)
    max_dirty_mib = min(1024, max_dirty_mib)
    return str(min_available_mem_mib), str(max_dirty_mib)


def sync_worker_tree(config, snapshot_tree, fresh_cache):
    cache_root = config.harness_state_dir / "cache"
    worker_tree = cache_root / "worker-tree"

    if fresh_cache and cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)

    if not (worker_tree / ".git").is_dir():
        _maybe_clone_local(snapshot_tree, worker_tree)

    _run(["git", "-C", str(worker_tree), "remote", "set-url", "origin", str(snapshot_tree)])
    _run([
        "git", "-C", str(worker_tree),
        "fetch", "--quiet", "origin", "+HEAD:refs/remotes/origin/local-vmksft-job",
    ])
    _run([
        "git", "-C", str(worker_tree),
        "checkout", "-q", "-B", "local-vmksft-job", "refs/remotes/origin/local-vmksft-job",
    ])
    _run([
        "git", "-C", str(worker_tree),
        "reset", "--quiet", "--hard", "refs/remotes/origin/local-vmksft-job",
    ])
    subprocess.run(
        ["git", "-C", str(worker_tree), "am", "--abort"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return worker_tree


def write_run_metadata(config, layout, job_record, branch_name, branch_date):
    targets = job_targets(job_record)
    data = {
        "run_id": layout.run_id,
        "executor_name": EXECUTOR_NAME,
        "targets": targets,
        "selected_tests": job_record.get("selected_tests", ""),
        "mode": job_record.get("requested_mode", ""),
        "source_tree": job_record.get("source_tree", ""),
        "source_branch": job_record.get("source_branch", ""),
        "source_head": job_record.get("source_head", ""),
        "source_base": job_record.get("source_base", ""),
        "actual_tree": job_record.get("snapshot_tree", ""),
        "published_branch": branch_name,
        "branch_date": branch_date,
        "public_host": config.public_host,
        "http_port": config.web_port,
        "run_public_prefix": layout.run_public_prefix,
        "job_id": job_record.get("job_id", ""),
    }
    write_text(layout.run_meta_json, json.dumps(data, indent=2, sort_keys=True))


def write_run_pages(layout, branch_name):
    write_redirect_page(
        layout.web_root / "index.html",
        f"/contest.html?branch={branch_name}",
        f"{EXECUTOR_NAME} run {layout.run_id}",
    )
    write_redirect_page(
        layout.web_root / "contest.html",
        f"/contest.html?branch={branch_name}",
        f"{EXECUTOR_NAME} run {layout.run_id}",
    )
    write_redirect_page(
        layout.executor_root / "index.html",
        "../index.html",
        f"{EXECUTOR_NAME} redirect",
    )
    write_redirect_page(
        layout.summary_html,
        "../index.html",
        f"{EXECUTOR_NAME} summary redirect",
    )


def build_executor_config(config, layout, job_record, worker_tree, branch_name, branch_date):
    host_mem_mib = host_mem_kb() // 1024
    options = job_record.get("options", {})
    targets = job_targets(job_record)
    selected_tests = job_record.get("selected_tests", "")
    skip_tests = job_record.get("skip_tests", "")
    guest_memory = resolve_guest_memory(options.get("memory", DEFAULT_MEMORY))
    guest_mem_mib = memory_to_mib(guest_memory)
    guest_cpus = resolve_guest_cpus(options.get("cpus", DEFAULT_CPUS))
    thread_cnt = resolve_threads(options.get("threads", DEFAULT_THREADS), host_mem_mib, guest_mem_mib)
    min_available_mem_mib, max_dirty_mib = resolve_scheduler_limits(host_mem_mib, guest_mem_mib)

    config_text = f"""[executor]
name = {EXECUTOR_NAME}
group = selftests-net
test = {targets}
deadline_minutes = 480

[run]
branch = {branch_name}
branch_date = {branch_date}

[local]
tree_path = {worker_tree}
base_path = {layout.executor_root}
json_path = jsons
results_path = results
live_status_path = {layout.live_status_json}
runtime_history_path = {config.harness_state_dir / "cache" / "test-runtime.json"}

[www]
url = http://{config.public_host}:{config.web_port}{layout.run_public_prefix}/{EXECUTOR_NAME}

[vm]
cpus = {guest_cpus}
mem = {guest_memory}
boot_timeout = 180
default_timeout = 1800
init_prompt = {options.get("init_prompt", DEFAULT_INIT_PROMPT)}
virtme_opt = --overlay-rwdir,{worker_tree}
build_reuse = true
build_clean = {options.get("build_clean", DEFAULT_BUILD_CLEAN)}

[ksft]
target = {targets}
only_tests = {selected_tests}
skip_tests = {skip_tests}
nested_tests = on

[cfg]
thread_cnt = {thread_cnt}
thread_spawn_delay = {DEFAULT_THREAD_SPAWN_DELAY}
scheduler_target_cpu_pct = {DEFAULT_TARGET_CPU_UTIL_PCT}
scheduler_sample_period_sec = {DEFAULT_SCHEDULER_SAMPLE_PERIOD_SEC}
scheduler_history_sec = {DEFAULT_SCHEDULER_HISTORY_SEC}
scheduler_up_hysteresis_pct = {DEFAULT_SCHEDULER_UP_HYSTERESIS_PCT}
scheduler_down_hysteresis_pct = {DEFAULT_SCHEDULER_DOWN_HYSTERESIS_PCT}
scheduler_min_available_mem_mib = {min_available_mem_mib}
scheduler_max_dirty_mib = {max_dirty_mib}
scheduler_vm_idle_shutdown_sec = {DEFAULT_VM_IDLE_SHUTDOWN_SEC}
runtime_history_cutoff_sec = {DEFAULT_RUNTIME_HISTORY_CUTOFF_SEC}
"""
    write_text(layout.config_path, config_text)


def render_results_page(config, layout, job_record):
    targets = job_targets(job_record)
    cmd = [
        "python3", str(config.script_dir / "bin" / "render-vmksft-results.py"),
        "--manifest", str(layout.manifest_path),
        "--summary-json", str(layout.summary_json),
        "--html", str(layout.summary_html),
        "--executor-name", EXECUTOR_NAME,
        "--mode", job_record.get("requested_mode", ""),
        "--targets", targets,
        "--source-tree", job_record.get("source_tree", ""),
        "--source-branch", job_record.get("source_branch", ""),
        "--source-head", job_record.get("source_head", ""),
        "--results-manifest-url", "./jsons/results.json",
        "--executor-log-url", "../executor.log",
        "--http-log-url", "",
        "--dashboard-url", "../index.html",
    ]
    _run(cmd)


def refresh_site_history(config):
    _run([
        "python3", str(config.script_dir / "bin" / "build-vmksft-history.py"),
        "--state-dir", str(config.harness_state_dir),
        "--site-root", str(config.site_root),
        "--executor-name", EXECUTOR_NAME,
    ])


def read_summary_exit_code(summary_json):
    with open(summary_json, "r", encoding="utf-8") as fp:
        return int(json.load(fp)["exit_code"])


def run_local_executor(config_path, executor_log_path):
    from local_vmksft_p import run_executor

    with open(executor_log_path, "w", encoding="utf-8") as fp:
        with contextlib.redirect_stdout(fp), contextlib.redirect_stderr(fp):
            run_executor([str(config_path)])


def execute_job(config, job_id):
    job_record = load_job_record(config, job_id)
    run_id = generate_run_id()
    layout = create_run_layout(config, run_id)
    prepare_run_layout(config, layout)

    branch_date = utc_now_iso_z()
    branch_name = published_branch_name(job_record.get("requested_mode", "committed"), run_id)
    write_run_metadata(config, layout, job_record, branch_name, branch_date)
    write_run_pages(layout, branch_name)

    worker_tree = sync_worker_tree(
        config,
        Path(job_record["snapshot_tree"]),
        bool(job_record.get("options", {}).get("fresh_cache")),
    )
    build_executor_config(config, layout, job_record, worker_tree, branch_name, branch_date)

    exit_code = 1
    try:
        run_local_executor(layout.config_path, layout.executor_log_path)
        if layout.manifest_path.is_file():
            render_results_page(config, layout, job_record)
            exit_code = read_summary_exit_code(layout.summary_json)
        else:
            write_infra_failure_page(
                layout.summary_html,
                "The executor completed without publishing a results manifest. Check executor.log.",
            )
    except Exception:
        with open(layout.executor_log_path, "a", encoding="utf-8") as fp:
            fp.write("\n")
            fp.write(traceback.format_exc())
        write_infra_failure_page(
            layout.summary_html,
            "The executor failed before producing a final summary. Check executor.log.",
        )
        exit_code = 1

    refresh_site_history(config)
    return exit_code


def utc_now_iso_z():
    return utc_now().isoformat()
