#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import argparse
import json
import sys

from pathlib import Path

LOCAL_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = LOCAL_ROOT / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))

from vmksft_service_lib import (
    DEFAULT_BUILD_CLEAN,
    DEFAULT_CPUS,
    DEFAULT_INIT_PROMPT,
    DEFAULT_MEMORY,
    DEFAULT_MODE,
    DEFAULT_THREADS,
    JobOptions,
    cancel_queued_job,
    enqueue_job,
    iter_job_records,
    load_job_record,
    load_runtime_config,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Submit and inspect local vmksft service jobs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="Freeze the current tree and enqueue a job")
    submit.add_argument("--mode", choices=["committed", "dirty", "patches"], default=DEFAULT_MODE)
    submit.add_argument("--build-clean", choices=["always", "never", "config-change"],
                        default=DEFAULT_BUILD_CLEAN)
    submit.add_argument("--threads", default=DEFAULT_THREADS)
    submit.add_argument("--cpus", default=DEFAULT_CPUS)
    submit.add_argument("--memory", default=DEFAULT_MEMORY)
    submit.add_argument("--init-prompt", default=DEFAULT_INIT_PROMPT)
    submit.add_argument("--fresh-cache", action="store_true")

    subparsers.add_parser("list", help="List known jobs")

    show = subparsers.add_parser("show", help="Show one job as JSON")
    show.add_argument("job_id")

    cancel = subparsers.add_parser("cancel", help="Cancel a queued job")
    cancel.add_argument("job_id")

    return parser.parse_args()


def build_options(args):
    return JobOptions(
        mode=args.mode,
        build_clean=args.build_clean,
        threads=args.threads,
        cpus=args.cpus,
        memory=args.memory,
        init_prompt=args.init_prompt,
        fresh_cache=bool(args.fresh_cache),
    )


def print_job_json(record):
    print(json.dumps(record, indent=2, sort_keys=True))


def handle_submit(config, args):
    record = enqueue_job(config, build_options(args))
    state = record.get("state", {})
    print(f"queued {record['job_id']} status={state.get('status')} mode={record['requested_mode']}")
    print_job_json(record)
    return 0


def handle_list(config):
    records = iter_job_records(config)
    if not records:
        print("no jobs")
        return 0

    for record in records:
        state = record.get("state", {})
        print(
            f"{record['job_id']} "
            f"status={state.get('status', '')} "
            f"mode={record.get('requested_mode', '')} "
            f"submitted={record.get('submitted_at', '')}"
        )
    return 0


def handle_show(config, job_id):
    print_job_json(load_job_record(config, job_id))
    return 0


def handle_cancel(config, job_id):
    if cancel_queued_job(config, job_id):
        print(f"cancelled {job_id}")
        return 0
    print(f"job is not queued: {job_id}", file=sys.stderr)
    return 1


def main():
    args = parse_args()
    config = load_runtime_config(__file__)

    try:
        if args.command == "submit":
            return handle_submit(config, args)
        if args.command == "list":
            return handle_list(config)
        if args.command == "show":
            return handle_show(config, args.job_id)
        if args.command == "cancel":
            return handle_cancel(config, args.job_id)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
