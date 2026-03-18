#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse


PENDING_STATUSES = {
    "queued",
    "running",
    "retry-queued",
    "retry-running",
    "pending",
    "building",
    "complete",
    "build-failed",
}

FINAL_RESULTS = {"pass", "skip", "warn", "fail"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build local harness history plus contest-compatible result JSON.",
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
    if result in PENDING_STATUSES:
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


def contest_branch_name(branch, run_id):
    base = branch or "local-vmksft-net"

    if run_id and run_id in base:
        return base
    if not run_id:
        return base
    return f"{base}-{run_id}"


def load_final_detail(executor_root):
    manifest = load_json(executor_root / "jsons" / "results.json")
    if not isinstance(manifest, list):
        return None

    for entry in reversed(manifest):
        if not isinstance(entry, dict):
            continue

        detail_url = entry.get("url")
        if not detail_url:
            continue

        detail_name = Path(urlparse(detail_url).path).name
        if not detail_name:
            continue

        detail = load_json(executor_root / "jsons" / detail_name)
        if detail:
            return detail

    return None


def normalize_final_result(result):
    normalized = dict(result)

    if normalized.get("result") not in FINAL_RESULTS:
        normalized["result"] = "warn"
    if normalized.get("retry") not in FINAL_RESULTS:
        normalized.pop("retry", None)

    if "results" in normalized and isinstance(normalized["results"], list):
        normalized["results"] = [normalize_final_result(entry) for entry in normalized["results"]]

    return normalized


def normalize_live_value(value):
    if value in FINAL_RESULTS:
        return value
    if value:
        return "warn"
    return ""


def live_result_from_test(test):
    result = {
        "group": test.get("group", ""),
        "test": test.get("test", ""),
        "result": normalize_live_value(test.get("result")) or normalize_live_value(test.get("status")) or "warn",
        "link": test.get("log_url", ""),
    }

    if test.get("retry") or test.get("status") in {"retry-running", "retry-queued"}:
        result["retry"] = normalize_live_value(test.get("retry")) or "warn"

    if "time" in test:
        result["time"] = test.get("time")

    return result


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

    published_branch = meta.get("published_branch") or (live_status or {}).get("branch", "")
    entry = {
        "run_id": run_id,
        "current": run_id == latest_run_id,
        "branch": published_branch,
        "ui_branch": contest_branch_name(published_branch, run_id),
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


def build_contest_entry(run_dir, executor_name):
    run_id = run_dir.name
    web_root = run_dir / "www"
    meta = load_json(web_root / "run-meta.json") or {}
    executor_root = web_root / executor_name
    live_status = load_json(executor_root / "live-status.json")
    detail = load_final_detail(executor_root)

    if not meta and not live_status and not detail:
        return None

    published_branch = meta.get("published_branch")
    if not published_branch:
        if detail:
            published_branch = detail.get("branch", "")
        else:
            published_branch = (live_status or {}).get("branch", "")

    branch = contest_branch_name(published_branch, run_id)
    remote = meta.get("source_branch") or meta.get("mode") or "local"

    if detail:
        results = [normalize_final_result(result) for result in detail.get("results", [])]
        start = detail.get("start", "")
        end = detail.get("end", "") or detail.get("start", "")
        executor = detail.get("executor", executor_name)
    elif live_status:
        results = [live_result_from_test(test) for test in live_status.get("tests", [])]
        start = live_status.get("start", "")
        end = live_status.get("updated", "") or live_status.get("start", "")
        executor = live_status.get("executor", executor_name)
    else:
        results = []
        start = meta.get("branch_date", "")
        end = meta.get("branch_date", "")
        executor = executor_name

    return {
        "branch": branch,
        "branch_url": f"/runs/{run_id}/index.html",
        "remote": remote,
        "executor": executor,
        "start": start,
        "end": end,
        "summary_url": f"/runs/{run_id}/{executor_name}/summary.html",
        "results": results,
    }


def entry_time(value):
    stamp = value.get("end") or value.get("start") or ""

    try:
        return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.UTC)


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
    contest_rows = []
    for run_dir in run_dirs:
        entry = build_run_entry(run_dir, args.executor_name, latest_run_id)
        if entry:
            runs.append(entry)

        contest_entry = build_contest_entry(run_dir, args.executor_name)
        if contest_entry:
            contest_rows.append(contest_entry)

    contest_rows.sort(key=entry_time, reverse=True)

    payload = {
        "generated": dt.datetime.now(dt.UTC).isoformat(),
        "latest_run_id": latest_run_id,
        "runs": runs,
    }
    write_json(site_root / "history.json", payload)
    write_json(site_root / "contest" / "all-results.json", contest_rows)
    write_json(site_root / "contest" / "filters.json", {"ignore-results": []})


if __name__ == "__main__":
    main()
