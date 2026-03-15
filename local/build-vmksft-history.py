#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime as dt
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build local run history JSON for the custom vmksft dashboard.",
    )
    parser.add_argument("--state-dir", required=True,
                        help="Local harness state directory")
    parser.add_argument("--site-root", required=True,
                        help="Stable site docroot where history.json should be written")
    parser.add_argument("--executor-name", required=True,
                        help="Executor directory name under each run web root")
    return parser.parse_args()


def load_json(path):
    try:
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def result_bucket(result):
    if result in {"queued", "running", "retry-queued", "retry-running",
                  "pending", "building", "complete", "build-failed"}:
        return "pending"
    return result


def overall_from_live(live_status):
    if not live_status:
        return "pending"

    any_fail = False
    any_pass = False
    any_skip = False
    any_pending = False

    for test in live_status.get("tests", []):
        status = result_bucket(test.get("status"))
        result = result_bucket(test.get("result"))
        retry = result_bucket(test.get("retry"))

        for value in (status, result, retry):
            if not value:
                continue
            if value == "fail":
                any_fail = True
            elif value == "pass":
                any_pass = True
            elif value == "skip":
                any_skip = True
            elif value == "pending":
                any_pending = True

    if live_status.get("status") in {"building", "running"}:
        any_pending = True

    if any_pending:
        return "pending"
    if any_fail:
        return "fail"
    if any_pass:
        return "pass"
    if any_skip:
        return "skip"
    return "pending"


def summary_counts(summary):
    counts = dict(summary.get("counts", {})) if summary else {}
    nested_total = summary.get("nested_total", 0) if summary else 0
    return counts, nested_total


def live_counts(live_status):
    counts = dict(live_status.get("counts", {})) if live_status else {}
    return counts


def path_mtime_iso(path):
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).isoformat()
    except FileNotFoundError:
        return ""


def build_run_entry(run_dir, executor_name, latest_run_id):
    run_id = run_dir.name
    web_root = run_dir / "www"
    meta = load_json(web_root / "run-meta.json") or {}
    executor_root = web_root / executor_name
    live_status = load_json(executor_root / "live-status.json")
    summary = load_json(executor_root / "summary.json")

    if not meta and not live_status and not summary:
        return None

    counts = summary_counts(summary)[0] if summary else live_counts(live_status)
    test_total = 0
    if live_status and live_status.get("tests"):
        test_total = len(live_status["tests"])
    elif counts:
        test_total = sum(counts.values())

    nested_total = summary.get("nested_total", 0) if summary else 0
    if summary:
        overall = summary.get("overall", "pending")
    else:
        overall = overall_from_live(live_status)

    status = "pending"
    build_status = ""
    updated = ""
    finished = False
    if live_status:
        status = live_status.get("status", status)
        build_status = live_status.get("build", {}).get("status", "")
        updated = live_status.get("updated", "") or live_status.get("start", "")
        finished = bool(live_status.get("finished"))
    elif summary:
        status = "complete"
        updated = path_mtime_iso(executor_root / "summary.json")
        finished = True

    entry = {
        "run_id": run_id,
        "current": run_id == latest_run_id,
        "branch": meta.get("published_branch") or (live_status or {}).get("branch", ""),
        "branch_date": meta.get("branch_date") or (live_status or {}).get("start", ""),
        "mode": meta.get("mode", ""),
        "source_branch": meta.get("source_branch", ""),
        "source_head": meta.get("source_head", ""),
        "targets": meta.get("targets", ""),
        "status": status,
        "build_status": build_status,
        "overall": overall,
        "counts": counts,
        "test_total": test_total,
        "nested_total": nested_total,
        "updated": updated,
        "finished": finished,
        "dashboard_url": f"/runs/{run_id}/index.html",
        "summary_url": f"/runs/{run_id}/{executor_name}/summary.html",
        "manifest_url": f"/runs/{run_id}/{executor_name}/jsons/results.json",
        "live_status_url": f"/runs/{run_id}/{executor_name}/live-status.json",
        "results_url": f"/runs/{run_id}/{executor_name}/results/",
        "executor_log_url": f"/runs/{run_id}/executor.log",
        "http_log_url": f"/runs/{run_id}/http-server.log",
        "config_url": f"/runs/{run_id}/vmksft-net-local.ini",
    }
    return entry


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".new")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, sort_keys=True)
    tmp.replace(path)


def main():
    args = parse_args()
    state_dir = Path(args.state_dir)
    site_root = Path(args.site_root)
    runs_dir = state_dir / "runs"

    run_dirs = sorted(
        (path for path in runs_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    ) if runs_dir.exists() else []

    latest_run_id = run_dirs[0].name if run_dirs else ""
    runs = []
    for run_dir in run_dirs:
        entry = build_run_entry(run_dir, args.executor_name, latest_run_id)
        if entry:
            runs.append(entry)

    payload = {
        "generated": dt.datetime.now(dt.UTC).isoformat(),
        "latest_run_id": latest_run_id,
        "runs": runs,
    }
    write_json(site_root / "history.json", payload)


if __name__ == "__main__":
    main()
