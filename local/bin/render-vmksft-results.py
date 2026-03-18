#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import datetime as dt
import json
import os
import urllib.parse


RESULT_ORDER = {
    "fail": 0,
    "skip": 1,
    "pass": 2,
}

def parse_args():
    parser = argparse.ArgumentParser(
        description="Build JSON data and a redirect shell for the local vmksft summary page.",
    )
    parser.add_argument("--manifest", required=True,
                        help="Path to vmksft results.json manifest")
    parser.add_argument("--summary-json", required=True,
                        help="Where to write the computed summary JSON")
    parser.add_argument("--html", required=True,
                        help="Where to write the static summary HTML shell")
    parser.add_argument("--executor-name", required=True,
                        help="Executor name shown in the report header")
    parser.add_argument("--mode", required=True,
                        help="Source mode used for the run")
    parser.add_argument("--targets", required=True,
                        help="Configured ksft TARGETS string")
    parser.add_argument("--source-tree", required=True,
                        help="Kernel tree path used as the source")
    parser.add_argument("--source-branch", required=True,
                        help="Source branch name shown in the report header")
    parser.add_argument("--source-head", required=True,
                        help="Source HEAD commit shown in the report header")
    parser.add_argument("--results-manifest-url", required=True,
                        help="Link to the manifest JSON from the rendered page")
    parser.add_argument("--executor-log-url", required=True,
                        help="Link to the executor log from the rendered page")
    parser.add_argument("--http-log-url", required=True,
                        help="Link to the HTTP server log from the rendered page")
    parser.add_argument("--dashboard-url", required=True,
                        help="Link back to the live dashboard page")
    return parser.parse_args()


def load_latest_result(manifest_path):
    with open(manifest_path, "r", encoding="utf-8") as fp:
        manifest = json.load(fp)

    if not manifest:
        raise RuntimeError("results manifest is empty")

    entry = None
    for candidate in reversed(manifest):
        if candidate.get("url"):
            entry = candidate
            break

    if entry is None:
        raise RuntimeError("results manifest does not contain a published run URL")

    detail_name = os.path.basename(urllib.parse.urlparse(entry["url"]).path)
    detail_path = os.path.join(os.path.dirname(manifest_path), detail_name)

    with open(detail_path, "r", encoding="utf-8") as fp:
        detail = json.load(fp)

    return entry, detail_path, detail


def count_results(results):
    counts = {}
    retry_failures = 0
    nested_counts = {}
    nested_total = 0

    for result in results:
        outcome = result.get("result", "unknown")
        counts[outcome] = counts.get(outcome, 0) + 1

        if result.get("retry") == "fail":
            retry_failures += 1

        for nested in result.get("results", []):
            nested_outcome = nested.get("result", "unknown")
            nested_counts[nested_outcome] = nested_counts.get(nested_outcome, 0) + 1
            nested_total += 1

    overall = "pass"
    exit_code = 0
    if counts.get("fail", 0) or retry_failures:
        overall = "fail"
        exit_code = 1
    elif counts and counts.get("pass", 0) == 0 and counts.get("skip", 0):
        overall = "skip"

    return {
        "counts": counts,
        "retry_failures": retry_failures,
        "nested_counts": nested_counts,
        "nested_total": nested_total,
        "overall": overall,
        "exit_code": exit_code,
    }


def parse_duration(detail):
    start = detail.get("start")
    end = detail.get("end")
    if not start or not end:
        return ""

    try:
        start_dt = dt.datetime.fromisoformat(start)
        end_dt = dt.datetime.fromisoformat(end)
    except ValueError:
        return ""

    seconds = (end_dt - start_dt).total_seconds()
    if seconds < 0:
        return ""
    return f"{seconds:.1f}s"


def summary_payload(args, entry, detail_path, detail):
    detail_name = os.path.basename(detail_path)
    summary = count_results(detail.get("results", []))
    summary.update({
        "branch": detail.get("branch", ""),
        "executor": detail.get("executor", args.executor_name),
        "mode": args.mode,
        "targets": args.targets,
        "source_tree": args.source_tree,
        "source_branch": args.source_branch,
        "source_head": args.source_head,
        "started": detail.get("start", ""),
        "finished": detail.get("end", ""),
        "duration": parse_duration(detail),
        "detail_path": detail_path,
        "detail_url": entry["url"],
        "detail_json_name": detail_name,
        "detail_json_relative_url": f"./jsons/{detail_name}",
        "results_manifest_url": args.results_manifest_url,
        "executor_log_url": args.executor_log_url,
        "http_log_url": args.http_log_url,
        "dashboard_url": args.dashboard_url,
        "run_link": detail.get("link", ""),
    })
    return summary


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, sort_keys=True)


def write_summary_redirect(path, dashboard_url):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url={dashboard_url}">
  <title>vmksft summary redirect</title>
</head>
<body>
  <p>Redirecting to the run dashboard. If the redirect does not happen, open <a href="{dashboard_url}">{dashboard_url}</a>.</p>
</body>
</html>
"""
        )


def main():
    args = parse_args()
    entry, detail_path, detail = load_latest_result(args.manifest)
    summary = summary_payload(args, entry, detail_path, detail)

    write_json(args.summary_json, summary)
    write_summary_redirect(args.html, args.dashboard_url)

    print("Result summary:")
    print(f"  overall: {summary['overall']}")
    for key in sorted(summary["counts"], key=lambda item: RESULT_ORDER.get(item, 99)):
        print(f"  {key}: {summary['counts'][key]}")
    print(f"  retry_failures: {summary['retry_failures']}")
    print(f"  detail_json: {detail_path}")
    print(f"  html_summary: {args.html} -> {args.dashboard_url}")


if __name__ == "__main__":
    main()
