#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import contextlib
import dataclasses
import datetime as dt
import fcntl
import json
import os
import shutil
import subprocess
import uuid

from pathlib import Path


STATUS_DIRS = ("running", "complete", "failed", "cancelled")
DEFAULT_MODE = "committed"
DEFAULT_BUILD_CLEAN = "config-change"
DEFAULT_THREADS = "auto"
DEFAULT_CPUS = "auto"
DEFAULT_MEMORY = "auto"
DEFAULT_INIT_PROMPT = "#"
DEFAULT_WEB_PORT = 8888
DEFAULT_TARGETS = "net net/packetdrill drivers/net/netdevsim"


@dataclasses.dataclass(frozen=True)
class RuntimeConfig:
    script_dir: Path
    kernel_tree: Path
    harness_state_dir: Path
    public_host: str
    web_port: int
    targets: str
    skip_tests: str

    @property
    def site_root(self):
        return self.harness_state_dir / "site"

    @property
    def service_root(self):
        return self.harness_state_dir / "service"

    @property
    def jobs_root(self):
        return self.service_root / "jobs"

    @property
    def queue_root(self):
        return self.service_root / "queue"

    @property
    def status_lock_path(self):
        return self.service_root / "queue.lock"

    @property
    def public_status_dir(self):
        return self.site_root / "service"

    @property
    def public_status_json(self):
        return self.public_status_dir / "status.json"

    @property
    def public_jobs_json(self):
        return self.public_status_dir / "jobs.json"

    @property
    def public_service_index(self):
        return self.public_status_dir / "index.html"

    @property
    def runs_root(self):
        return self.harness_state_dir / "runs"


@dataclasses.dataclass(frozen=True)
class JobOptions:
    mode: str = DEFAULT_MODE
    build_clean: str = DEFAULT_BUILD_CLEAN
    threads: str = DEFAULT_THREADS
    cpus: str = DEFAULT_CPUS
    memory: str = DEFAULT_MEMORY
    init_prompt: str = DEFAULT_INIT_PROMPT
    fresh_cache: bool = False
    patch_dir: str = ""
    tests: str = ""


def utc_now():
    return dt.datetime.now(dt.UTC)


def utc_now_iso():
    return utc_now().isoformat()


def _strip_quotes(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_local_env(script_dir):
    env_path = script_dir / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _strip_quotes(value.strip())


def resolve_path(value, base_dir):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def find_local_root(script_path):
    resolved = Path(script_path).resolve()
    if resolved.parent.name in {"bin", "lib", "tests", "web", "docs", "systemd"}:
        return resolved.parent.parent
    return resolved.parent


def load_runtime_config(script_path):
    script_dir = find_local_root(script_path)
    load_local_env(script_dir)

    kernel_tree = resolve_path(os.environ.get("NIPA_KERNEL_TREE", "/tmp/nipa-missing-kernel-tree"),
                               script_dir)
    state_root = resolve_path(os.environ.get("NIPA_STATE_DIR", "./state"), script_dir)
    public_host = os.environ.get("NIPA_PUBLIC_HOST", "localhost")
    web_port = int(os.environ.get("NIPA_WEB_PORT", DEFAULT_WEB_PORT))
    targets = os.environ.get("NIPA_VMKSFT_TARGETS", DEFAULT_TARGETS).strip() or DEFAULT_TARGETS
    skip_tests = os.environ.get("NIPA_VMKSFT_SKIP_TESTS", "").strip()

    return RuntimeConfig(
        script_dir=script_dir,
        kernel_tree=kernel_tree,
        harness_state_dir=state_root / "vmksft-net",
        public_host=public_host,
        web_port=web_port,
        targets=targets,
        skip_tests=skip_tests,
    )


def ensure_layout(config):
    config.jobs_root.mkdir(parents=True, exist_ok=True)
    config.queue_root.mkdir(parents=True, exist_ok=True)
    config.public_status_dir.mkdir(parents=True, exist_ok=True)
    config.runs_root.mkdir(parents=True, exist_ok=True)
    for name in STATUS_DIRS:
        (config.service_root / name).mkdir(parents=True, exist_ok=True)

    if not config.public_service_index.exists():
        config.public_service_index.write_text(
            """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>vmksft service status</title>
</head>
<body>
  <p><a href="./status.json">status.json</a></p>
  <p><a href="./jobs.json">jobs.json</a></p>
</body>
</html>
""",
            encoding="utf-8",
        )


@contextlib.contextmanager
def state_lock(config):
    ensure_layout(config)
    with open(config.status_lock_path, "a+", encoding="utf-8") as fp:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        try:
            yield fp
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _run(cmd, cwd=None, input_text=None, capture_output=False):
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=capture_output,
        check=True,
    )


def _maybe_clone_local(source, destination):
    try:
        _run(["git", "clone", "--local", "--quiet", str(source), str(destination)],
             capture_output=True)
    except subprocess.CalledProcessError:
        _run(["git", "clone", "--quiet", str(source), str(destination)])


def git_output(repo, *args):
    proc = _run(["git", "-C", str(repo), *args], capture_output=True)
    return proc.stdout.strip()


def git_current_branch(repo):
    branch = git_output(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if branch:
        return branch
    return "(detached HEAD)"


def git_head(repo):
    return git_output(repo, "rev-parse", "HEAD")


def git_head_parent(repo):
    try:
        return git_output(repo, "rev-parse", "HEAD^")
    except subprocess.CalledProcessError:
        return git_head(repo)


def clone_snapshot_repo(source_repo, destination, head):
    _maybe_clone_local(source_repo, destination)
    _run(["git", "-C", str(destination), "checkout", "-q", "--detach", head])
    _run(["git", "-C", str(destination), "reset", "--quiet", "--hard", head])
    _run(["git", "-C", str(destination), "clean", "-fdx", "-q"])


def _git_local_commit(repo, message):
    _run([
        "git", "-C", str(repo),
        "-c", "commit.gpgSign=false",
        "-c", "core.hooksPath=/dev/null",
        "-c", "user.name=local-vmksft",
        "-c", "user.email=local-vmksft@nipa.local",
        "commit", "--quiet", "-m", message,
    ])


def copy_untracked_files(src_root, dst_root):
    proc = subprocess.run(
        ["git", "-C", str(src_root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    )

    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        rel = entry.decode("utf-8", "surrogateescape")
        src = src_root / rel
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        if src.is_symlink():
            dst.symlink_to(os.readlink(src))
        else:
            shutil.copy2(src, dst, follow_symlinks=False)


def patch_has_diff(path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if (line.startswith("diff --git ") or line.startswith("--- ") or
                line.startswith("+++ ") or line.startswith("Index: ")):
            return True
    return False


def patch_is_mail(path):
    first = ""
    subject = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not first:
            first = line
        if line.startswith("Subject: "):
            subject = True
        if first and subject:
            break
    return first.startswith("From ") and subject


def collect_patch_files(patch_dir):
    entries = []
    for path in sorted(patch_dir.iterdir()):
        if path.is_file() and path.suffix in (".patch", ".mbox"):
            entries.append(path)
    if not entries:
        raise FileNotFoundError(f"no .patch or .mbox files found under {patch_dir}")
    return entries


def apply_patch_series(repo, patch_dir):
    patch_files = collect_patch_files(patch_dir)
    applied = 0
    for patch in patch_files:
        if not patch_has_diff(patch):
            continue
        if patch_is_mail(patch):
            try:
                _run([
                    "git", "-C", str(repo),
                    "-c", "commit.gpgSign=false",
                    "-c", "core.hooksPath=/dev/null",
                    "-c", "user.name=local-vmksft",
                    "-c", "user.email=local-vmksft@nipa.local",
                    "am", "--quiet", "-3", "--keep-cr", "--whitespace=nowarn", str(patch),
                ])
            except subprocess.CalledProcessError:
                subprocess.run(["git", "-C", str(repo), "am", "--abort"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                raise
        else:
            _run(["git", "-C", str(repo), "apply", "--binary", "--index", str(patch)])
            _git_local_commit(repo, f"local-vmksft patch snapshot: {patch.name}")
        applied += 1

    if applied == 0:
        raise RuntimeError(f"no patch files with diff content found under {patch_dir}")
    return patch_files, applied


def _finalize_snapshot_commit(repo, message):
    _run(["git", "-C", str(repo), "add", "-A"])
    if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"],
                      check=False).returncode == 0:
        return git_head(repo), False
    _git_local_commit(repo, message)
    return git_head(repo), True


def _prepare_committed_snapshot(job_dir, kernel_tree, source_head):
    snapshot_tree = job_dir / "source-tree"
    clone_snapshot_repo(kernel_tree, snapshot_tree, source_head)
    return snapshot_tree, source_head, 0


def _prepare_dirty_snapshot(job_dir, kernel_tree, source_head):
    snapshot_tree = job_dir / "source-tree"
    clone_snapshot_repo(kernel_tree, snapshot_tree, source_head)

    dirty_patch = subprocess.run(
        ["git", "-C", str(kernel_tree), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
        capture_output=True,
        check=True,
    )
    if dirty_patch.stdout:
        subprocess.run(
            ["git", "-C", str(snapshot_tree), "apply", "--binary", "--index", "-"],
            input=dirty_patch.stdout,
            check=True,
        )
    copy_untracked_files(kernel_tree, snapshot_tree)
    snapshot_head, _ = _finalize_snapshot_commit(snapshot_tree, "local-vmksft dirty snapshot")
    return snapshot_tree, snapshot_head, 0


def _prepare_patches_snapshot(job_dir, kernel_tree, patch_dir, source_head):
    snapshot_tree = job_dir / "source-tree"
    copied_patch_dir = job_dir / "patches"
    clone_snapshot_repo(kernel_tree, snapshot_tree, source_head)
    copied_patch_dir.mkdir(parents=True, exist_ok=True)

    patch_files = collect_patch_files(patch_dir)
    for patch in patch_files:
        shutil.copy2(patch, copied_patch_dir / patch.name)

    _copied_patch_files, patch_count = apply_patch_series(snapshot_tree, copied_patch_dir)
    return snapshot_tree, git_head(snapshot_tree), patch_count


def generate_job_id():
    ts = utc_now().strftime("%Y%m%d-%H%M%S")
    return f"job-{ts}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def make_queue_entry_name(submitted_ns, job_id):
    return f"{submitted_ns:020d}--{job_id}"


def _job_dir(config, job_id):
    return config.jobs_root / job_id


def _job_json_path(config, job_id):
    return _job_dir(config, job_id) / "job.json"


def _job_state_path(config, job_id):
    return _job_dir(config, job_id) / "state.json"


def load_job_record(config, job_id):
    job = load_json(_job_json_path(config, job_id), default={}) or {}
    state = load_json(_job_state_path(config, job_id), default={}) or {}
    if not job and not state:
        raise FileNotFoundError(f"unknown job: {job_id}")
    record = dict(job)
    record["state"] = state
    return record


def iter_job_records(config):
    ensure_layout(config)
    records = []
    for job_dir in config.jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        job = load_json(job_dir / "job.json", default={}) or {}
        state = load_json(job_dir / "state.json", default={}) or {}
        if not job:
            continue
        record = dict(job)
        record["state"] = state
        records.append(record)
    records.sort(key=lambda record: record.get("submitted_ns", 0))
    return records


def _remove_job_links(config, job_id):
    for entry in list(config.queue_root.iterdir()):
        if entry.is_symlink() and entry.resolve() == _job_dir(config, job_id):
            entry.unlink()
    for name in STATUS_DIRS:
        path = config.service_root / name / job_id
        if path.is_symlink() or path.exists():
            path.unlink()


def _create_symlink(link_path, target):
    rel_target = os.path.relpath(target, start=link_path.parent)
    link_path.symlink_to(rel_target)


def update_job_state(config, job_id, status, **changes):
    job_state_path = _job_state_path(config, job_id)
    state = load_json(job_state_path, default={}) or {}
    state["status"] = status
    state["updated_at"] = utc_now_iso()
    state.update(changes)
    write_json_atomic(job_state_path, state)
    return state


def _sync_job_links(config, job_id, status, queue_entry=None):
    _remove_job_links(config, job_id)
    if status == "queued":
        if not queue_entry:
            raise ValueError("queue_entry is required for queued jobs")
        _create_symlink(config.queue_root / queue_entry, _job_dir(config, job_id))
        return
    if status in STATUS_DIRS:
        _create_symlink(config.service_root / status / job_id, _job_dir(config, job_id))


def enqueue_job(config, options):
    ensure_layout(config)
    if not config.kernel_tree.is_dir():
        raise FileNotFoundError(f"kernel tree not found: {config.kernel_tree}")
    if not (config.kernel_tree / "tools/testing/selftests/net").is_dir():
        raise FileNotFoundError(f"tree does not look like a kernel checkout: {config.kernel_tree}")

    patch_dir = None
    if options.patch_dir:
        patch_dir = resolve_path(options.patch_dir, Path.cwd())
    if options.mode == "patches":
        if patch_dir is None:
            raise ValueError("--patch-dir is required for patches mode")
        if not patch_dir.is_dir():
            raise FileNotFoundError(f"patch directory not found: {patch_dir}")
    elif patch_dir is not None:
        raise ValueError("--patch-dir is only valid with patches mode")

    job_id = generate_job_id()
    job_dir = _job_dir(config, job_id)
    submitted = utc_now()
    submitted_iso = submitted.isoformat()
    submitted_ns = time_ns()
    job_dir.mkdir(parents=True, exist_ok=False)

    source_head = git_head(config.kernel_tree)
    source_base = git_head_parent(config.kernel_tree)
    source_branch = git_current_branch(config.kernel_tree)

    try:
        if options.mode == "committed":
            snapshot_tree, snapshot_head, patch_count = _prepare_committed_snapshot(
                job_dir, config.kernel_tree, source_head)
        elif options.mode == "dirty":
            snapshot_tree, snapshot_head, patch_count = _prepare_dirty_snapshot(
                job_dir, config.kernel_tree, source_head)
        elif options.mode == "patches":
            snapshot_tree, snapshot_head, patch_count = _prepare_patches_snapshot(
                job_dir, config.kernel_tree, patch_dir, source_head)
        else:
            raise ValueError(f"unsupported mode: {options.mode}")
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise

    queue_entry = make_queue_entry_name(submitted_ns, job_id)
    job_data = {
        "job_id": job_id,
        "submitted_at": submitted_iso,
        "submitted_ns": submitted_ns,
        "requested_mode": options.mode,
        "source_tree": str(config.kernel_tree),
        "source_branch": source_branch,
        "source_head": source_head,
        "source_base": source_base,
        "snapshot_tree": str(snapshot_tree),
        "snapshot_head": snapshot_head,
        "targets": config.targets,
        "patch_dir": str(patch_dir) if patch_dir is not None else "",
        "patch_count": patch_count,
        "selected_tests": options.tests.strip(),
        "skip_tests": config.skip_tests,
        "options": dataclasses.asdict(options),
    }
    state_data = {
        "job_id": job_id,
        "status": "queued",
        "submitted_at": submitted_iso,
        "updated_at": submitted_iso,
        "queue_entry": queue_entry,
        "started_at": "",
        "finished_at": "",
        "detail": "waiting for service",
        "run_exit_code": None,
        "run_id": "",
        "run_url": "",
        "summary_url": "",
        "runner_pid": None,
    }
    write_json_atomic(_job_json_path(config, job_id), job_data)
    with state_lock(config):
        write_json_atomic(_job_state_path(config, job_id), state_data)
        _sync_job_links(config, job_id, "queued", queue_entry=queue_entry)
    return load_job_record(config, job_id)


def next_queued_job(config):
    with state_lock(config):
        queue_entries = [entry for entry in config.queue_root.iterdir() if entry.is_symlink()]
        queue_entries.sort(key=lambda entry: entry.name)
        if not queue_entries:
            return None
        entry = queue_entries[0]
        job_id = entry.resolve().name
        started_at = utc_now_iso()
        state = update_job_state(
            config,
            job_id,
            "running",
            queue_entry=entry.name,
            started_at=started_at,
            detail="starting job process",
        )
        _sync_job_links(config, job_id, "running")
        record = load_job_record(config, job_id)
        record["state"] = state
        return record


def set_job_runner_pid(config, job_id, pid):
    with state_lock(config):
        return update_job_state(config, job_id, "running", runner_pid=pid, detail="job process active")


def discover_run_for_job(config, job_id):
    if not config.runs_root.is_dir():
        return None

    candidates = []
    for run_dir in config.runs_root.iterdir():
        meta_path = run_dir / "www" / "run-meta.json"
        if meta_path.is_file():
            candidates.append((meta_path.stat().st_mtime, meta_path))
    candidates.sort(reverse=True)

    for _mtime, meta_path in candidates:
        meta = load_json(meta_path, default={}) or {}
        if meta.get("job_id") == job_id:
            run_id = meta.get("run_id") or meta_path.parents[1].name
            return {
                "run_id": run_id,
                "run_url": f"/runs/{run_id}/index.html",
                "summary_url": f"/runs/{run_id}/vmksft-net-local/summary.html",
            }
    return None


def finish_job(config, job_id, exit_code, detail):
    run_meta = discover_run_for_job(config, job_id) or {}
    status = "complete" if exit_code == 0 else "failed"
    with state_lock(config):
        state = update_job_state(
            config,
            job_id,
            status,
            finished_at=utc_now_iso(),
            detail=detail,
            run_exit_code=exit_code,
            run_id=run_meta.get("run_id", ""),
            run_url=run_meta.get("run_url", ""),
            summary_url=run_meta.get("summary_url", ""),
            runner_pid=None,
        )
        _sync_job_links(config, job_id, status)
        return state


def cancel_queued_job(config, job_id):
    with state_lock(config):
        state = load_json(_job_state_path(config, job_id), default={}) or {}
        if state.get("status") != "queued":
            return False
        update_job_state(
            config,
            job_id,
            "cancelled",
            finished_at=utc_now_iso(),
            detail="cancelled before execution",
            runner_pid=None,
        )
        _sync_job_links(config, job_id, "cancelled")
        return True


def recover_stale_running_jobs(config):
    with state_lock(config):
        ensure_layout(config)
        recovered = []
        for entry in config.service_root.joinpath("running").iterdir():
            if not entry.is_symlink():
                continue
            job_id = entry.name
            update_job_state(
                config,
                job_id,
                "failed",
                finished_at=utc_now_iso(),
                detail="service restarted while the job was running",
                run_exit_code=130,
                runner_pid=None,
            )
            _sync_job_links(config, job_id, "failed")
            recovered.append(job_id)
        return recovered


def time_ns():
    return time_now_ns()


def time_now_ns():
    return int(utc_now().timestamp() * 1_000_000_000)


def _job_public_record(record):
    state = record.get("state", {})
    result = {
        "job_id": record.get("job_id", ""),
        "requested_mode": record.get("requested_mode", ""),
        "targets": record.get("targets", ""),
        "selected_tests": record.get("selected_tests", ""),
        "source_branch": record.get("source_branch", ""),
        "source_head": record.get("source_head", ""),
        "snapshot_head": record.get("snapshot_head", ""),
        "submitted_at": record.get("submitted_at", ""),
        "patch_count": record.get("patch_count", 0),
        "options": record.get("options", {}),
        "status": state.get("status", ""),
        "detail": state.get("detail", ""),
        "started_at": state.get("started_at", ""),
        "finished_at": state.get("finished_at", ""),
        "updated_at": state.get("updated_at", ""),
        "run_exit_code": state.get("run_exit_code"),
        "run_id": state.get("run_id", ""),
        "run_url": state.get("run_url", ""),
        "summary_url": state.get("summary_url", ""),
    }
    return result


def write_public_status(config):
    ensure_layout(config)
    records = iter_job_records(config)
    public_jobs = [_job_public_record(record) for record in records]
    counts = {}
    running_job_id = ""
    for record in public_jobs:
        counts[record["status"]] = counts.get(record["status"], 0) + 1
        if record["status"] == "running":
            running_job_id = record["job_id"]

    status_data = {
        "updated_at": utc_now_iso(),
        "state_dir": str(config.harness_state_dir),
        "site_root": str(config.site_root),
        "queue_depth": counts.get("queued", 0),
        "running_job_id": running_job_id,
        "counts": counts,
        "public_base_url": f"http://{config.public_host}:{config.web_port}/",
    }
    write_json_atomic(config.public_status_json, status_data)
    write_json_atomic(config.public_jobs_json, public_jobs)
    return status_data
