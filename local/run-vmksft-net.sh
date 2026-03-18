#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

set -euo pipefail

SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
NIPA_ROOT="$(cd -P -- "${SCRIPT_DIR}/.." && pwd -P)"
readonly NIPA_ROOT
readonly UI_ROOT="${NIPA_ROOT}/ui"
readonly BIN_DIR="${SCRIPT_DIR}/bin"
readonly LIB_DIR="${SCRIPT_DIR}/lib"
readonly WEB_DIR="${SCRIPT_DIR}/web"
readonly DEFAULT_TREE="/home/wes/net"
readonly DEFAULT_STATE_DIR="${SCRIPT_DIR}/state/vmksft-net"
readonly DEFAULT_THREADS="auto"
readonly DEFAULT_CPUS="auto"
readonly DEFAULT_MEMORY="auto"
readonly DEFAULT_INIT_PROMPT="#"
readonly DEFAULT_HTTP_PORT=8888
readonly DEFAULT_PUBLIC_HOST="192.168.50.103"
readonly DEFAULT_INTERNAL_HTTP_BIND="0.0.0.0"
readonly DEFAULT_BRANCH_NAME="local-vmksft-net"
readonly DEFAULT_MODE="committed"
readonly DEFAULT_RESERVED_MEM_GB=8
readonly DEFAULT_VM_MEM_GB=2
readonly DEFAULT_THREAD_SPAWN_DELAY=0.5
readonly DEFAULT_TARGET_CPU_UTIL_PCT=70
readonly DEFAULT_SCHEDULER_HISTORY_SEC=5
readonly DEFAULT_SCHEDULER_SAMPLE_PERIOD_SEC=1
readonly DEFAULT_SCHEDULER_UP_HYSTERESIS_PCT=4
readonly DEFAULT_SCHEDULER_DOWN_HYSTERESIS_PCT=2
readonly DEFAULT_VM_IDLE_SHUTDOWN_SEC=15
readonly DEFAULT_BUILD_CLEAN="config-change"
readonly DEFAULT_DIRTY_COMMIT_MSG="local-vmksft dirty snapshot"
readonly DEFAULT_PATCH_COMMIT_PREFIX="local-vmksft patch snapshot"
readonly EXECUTOR_NAME="vmksft-net-local"
readonly CONTEST_HTML_TEMPLATE="${WEB_DIR}/contest.html"
readonly EXECUTOR_TARGET="net net/packetdrill drivers/net/netdevsim net/mptcp"
#readonly EXECUTOR_TARGET="net net/af_unix net/can net/forwarding net/hsr net/mptcp net/netfilter net/openvswitch net/ovpn net/packetdrill net/tcp_ao nci drivers/net/bonding drivers/net/netconsole drivers/net/netdevsim drivers/net/team drivers/net/virtio_net"


TREE="${DEFAULT_TREE}"
STATE_DIR="${DEFAULT_STATE_DIR}"
MODE="${DEFAULT_MODE}"
THREADS="${DEFAULT_THREADS}"
GUEST_CPUS="${DEFAULT_CPUS}"
GUEST_MEMORY="${DEFAULT_MEMORY}"
INIT_PROMPT="${DEFAULT_INIT_PROMPT}"
HTTP_PORT="${DEFAULT_HTTP_PORT}"
INTERNAL_HTTP_BIND="${DEFAULT_INTERNAL_HTTP_BIND}"
INTERNAL_HTTP_PORT=""
PUBLIC_HOST="${DEFAULT_PUBLIC_HOST}"
PATCH_DIR=""
BUILD_CLEAN="${DEFAULT_BUILD_CLEAN}"
EXPLAIN_ONLY=0
EXIT_WHEN_DONE=0
JOB_META_PATH=""

RUN_ID=""
RUN_DIR=""
CACHE_DIR=""
REMOTE_GIT=""
WORKER_TREE=""
MATERIALIZE_TREE=""
WEB_ROOT=""
EXECUTOR_ROOT=""
CONFIG_PATH=""
HTTP_PID=""
SITE_REFRESH_PID=""
SERVER_READY=0
FRESH_CACHE=0
HOST_CPUS=0
HOST_MEM_KB=0
HOST_MEM_MIB=0
GUEST_MEM_MIB=0
SCHEDULER_MIN_AVAILABLE_MEM_MIB=0
SCHEDULER_MAX_DIRTY_MIB=0
VIRTIOFSD_PATH=""
BRANCH_NAME=""
BRANCH_DATE=""
BRANCH_BASE=""
TREE_HEAD=""
TREE_BASE=""
DIRTY_PATCH=""
PATCH_FILES=()
PATCH_COUNT=0
SOURCE_BRANCH=""
SOURCE_TREE_DISPLAY=""
TREE_HEAD_DISPLAY=""
TREE_BASE_DISPLAY=""
MODE_LABEL=""
SERVICE_JOB_ID=""
EXECUTOR_INDEX=""
SUMMARY_JSON=""
MANIFEST_PATH=""
RUN_COMPLETE=0
RUN_RESULT_RC=0
LIVE_STATUS_JSON=""
SUMMARY_HTML=""
RUN_META_JSON=""
SITE_ROOT=""
SITE_RUNS_ROOT=""
SITE_REFRESH_LOG=""
RUN_PUBLIC_PREFIX=""

usage() {
	cat <<EOF
Usage: $(basename "$0") [options]

Run the local one-shot vmksft harness for TARGETS=${EXECUTOR_TARGET}.

Options:
  --tree PATH           Kernel tree to test. Default: ${DEFAULT_TREE}
  --state-dir PATH      Runtime state root. Default: ${DEFAULT_STATE_DIR}
  --mode MODE           Source mode: committed, dirty, or patches. Default: ${DEFAULT_MODE}
  --patch-dir PATH      Directory of .patch/.mbox files for --mode patches
  --build-clean MODE    Build cleaning policy: always, never, or config-change.
                        Default: ${DEFAULT_BUILD_CLEAN}
  --explain, -explain   Print the fully resolved execution plan and exit
  --threads N|auto      Maximum vmksft-p worker threads. The dynamic scheduler
                        only admits work up to this cap. Default: ${DEFAULT_THREADS}
  --cpus N|auto         Guest CPU count. Default: ${DEFAULT_CPUS}
  --memory SIZE|auto    Guest memory. Default: ${DEFAULT_MEMORY}
  --init-prompt STR     Initial guest prompt. Default: ${DEFAULT_INIT_PROMPT}
  --http-port N         Public HTTP port used in generated result URLs. Default: ${DEFAULT_HTTP_PORT}
  --internal-http-bind HOST
                        Bind address for the runner's private manifest server.
                        Default: ${DEFAULT_INTERNAL_HTTP_BIND}
  --internal-http-port N
                        Private manifest/results port for the runner's built-in
                        HTTP server. Default: same as --http-port
  --public-host HOST    Hostname or IP published in result URLs. Default: ${DEFAULT_PUBLIC_HOST}
  --job-meta PATH       Optional JSON metadata override for queued service jobs
  --exit-when-done      Exit after the run completes instead of keeping the
                        built-in HTTP server alive for manual browsing
  --fresh-cache         Drop the cached remote/worker tree before this run
  -h, --help            Show this help text
EOF
}

log() {
	printf '[local-vmksft-net] %s\n' "$*"
}

die() {
	printf '[local-vmksft-net] error: %s\n' "$*" >&2
	exit 1
}

need_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

require_value() {
	[[ $# -ge 2 ]] || die "missing value for $1"
}

find_virtiofsd() {
	local candidate
	local -a candidates=(
		"$(command -v virtiofsd 2>/dev/null || true)"
		"/usr/libexec/virtiofsd"
		"/usr/lib/virtiofsd/virtiofsd"
		"/usr/lib/virtiofsd"
		"/usr/lib/qemu/virtiofsd"
	)

	for candidate in "${candidates[@]}"; do
		[[ -n "${candidate}" ]] || continue
		if [[ -x "${candidate}" ]]; then
			printf '%s\n' "${candidate}"
			return 0
		fi
	done

	return 1
}

clone_local_repo() {
	local source="$1"
	local destination="$2"

	if ! git clone --local --quiet "${source}" "${destination}"; then
		git clone --quiet "${source}" "${destination}"
	fi
}

current_source_branch() {
	local branch

	branch="$(git -C "${TREE}" symbolic-ref --short -q HEAD || true)"
	if [[ -n "${branch}" ]]; then
		printf '%s\n' "${branch}"
	else
		printf '%s\n' "(detached HEAD)"
	fi
}

apply_job_meta() {
	[[ -n "${JOB_META_PATH}" ]] || return 0

	eval "$(
		python3 - "${JOB_META_PATH}" <<'PY'
import json
import shlex
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fp:
    data = json.load(fp)

mapping = {
    "requested_mode": "MODE_LABEL",
    "source_tree": "SOURCE_TREE_DISPLAY",
    "source_branch": "SOURCE_BRANCH",
    "source_head": "TREE_HEAD_DISPLAY",
    "source_base": "TREE_BASE_DISPLAY",
    "job_id": "SERVICE_JOB_ID",
    "patch_count": "PATCH_COUNT",
}

for src_key, dst_key in mapping.items():
    value = data.get(src_key, "")
    if value is None:
        value = ""
    print(f"{dst_key}={shlex.quote(str(value))}")
PY
	)"
}

host_mem_kb() {
	awk '/MemTotal/ { print $2 }' /proc/meminfo
}

memory_to_mib() {
	local value="${1^^}"

	case "${value}" in
	*[K])
		printf '%s\n' "$(( (${value%K} + 1023) / 1024 ))"
		;;
	*[M])
		printf '%s\n' "${value%M}"
		;;
	*[G])
		printf '%s\n' "$(( ${value%G} * 1024 ))"
		;;
	*[T])
		printf '%s\n' "$(( ${value%T} * 1024 * 1024 ))"
		;;
	*)
		return 1
		;;
	esac
}

resolve_guest_memory() {
	if [[ "${GUEST_MEMORY}" == "auto" ]]; then
		GUEST_MEMORY="${DEFAULT_VM_MEM_GB}G"
	fi

	memory_to_mib "${GUEST_MEMORY}" >/dev/null ||
		die "--memory must be auto or a size with K, M, G, or T suffix: ${GUEST_MEMORY}"
	GUEST_MEM_MIB="$(memory_to_mib "${GUEST_MEMORY}")"
}

resolve_guest_cpus() {
	if [[ "${GUEST_CPUS}" == "auto" ]]; then
		GUEST_CPUS=2
	fi

	[[ "${GUEST_CPUS}" =~ ^[0-9]+$ ]] ||
		die "--cpus must be auto or a non-negative integer: ${GUEST_CPUS}"
	(( GUEST_CPUS >= 1 )) || die "--cpus must be at least 1"
}

resolve_threads() {
	local guest_mem_mib
	local reserved_mem_mib
	local mem_limited

	if [[ "${THREADS}" != "auto" ]]; then
		[[ "${THREADS}" =~ ^[0-9]+$ ]] ||
			die "--threads must be auto or a non-negative integer: ${THREADS}"
		return 0
	fi

	guest_mem_mib="${GUEST_MEM_MIB}"

	reserved_mem_mib=$(( DEFAULT_RESERVED_MEM_GB * 1024 ))
	mem_limited=$(( (HOST_MEM_MIB - reserved_mem_mib) / guest_mem_mib ))
	if (( mem_limited < 1 )); then
		mem_limited=1
	fi

	THREADS="${mem_limited}"
}

resolve_scheduler_limits() {
	local reserved_mem_mib

	reserved_mem_mib=$(( DEFAULT_RESERVED_MEM_GB * 1024 ))
	if (( GUEST_MEM_MIB < reserved_mem_mib )); then
		SCHEDULER_MIN_AVAILABLE_MEM_MIB="${GUEST_MEM_MIB}"
	else
		SCHEDULER_MIN_AVAILABLE_MEM_MIB="${reserved_mem_mib}"
	fi
	if (( SCHEDULER_MIN_AVAILABLE_MEM_MIB < 1024 )); then
		SCHEDULER_MIN_AVAILABLE_MEM_MIB=1024
	fi
	if (( SCHEDULER_MIN_AVAILABLE_MEM_MIB > HOST_MEM_MIB / 2 )); then
		SCHEDULER_MIN_AVAILABLE_MEM_MIB=$(( HOST_MEM_MIB / 2 ))
	fi
	if (( SCHEDULER_MIN_AVAILABLE_MEM_MIB < 256 )); then
		SCHEDULER_MIN_AVAILABLE_MEM_MIB=256
	fi
	SCHEDULER_MAX_DIRTY_MIB=$(( GUEST_MEM_MIB / 2 ))
	if (( SCHEDULER_MAX_DIRTY_MIB < 256 )); then
		SCHEDULER_MAX_DIRTY_MIB=256
	fi
	if (( SCHEDULER_MAX_DIRTY_MIB > 1024 )); then
		SCHEDULER_MAX_DIRTY_MIB=1024
	fi
}

initialize_testing_remote() {
	if [[ ! -d "${REMOTE_GIT}" ]]; then
		log "initializing cached testing remote from ${TREE}: ${REMOTE_GIT}"
		if ! git clone --bare --local --quiet "${TREE}" "${REMOTE_GIT}"; then
			git clone --bare --quiet "${TREE}" "${REMOTE_GIT}"
		fi
	fi
}

publish_branch_from_repo() {
	local repo="$1"
	local ref="$2"
	local branch="$3"
	local remote_url="file://${REMOTE_GIT}"

	log "publishing ${branch} from ${repo}"
	git -C "${repo}" push --quiet --force "${remote_url}" "${ref}:refs/heads/${branch}"
	git --git-dir="${REMOTE_GIT}" symbolic-ref HEAD "refs/heads/${branch}"
}

initialize_materialize_repo() {
	if [[ ! -d "${MATERIALIZE_TREE}/.git" ]]; then
		log "creating cached materialize repo: ${MATERIALIZE_TREE}"
		clone_local_repo "${TREE}" "${MATERIALIZE_TREE}"
	fi
}

prepare_materialize_repo() {
	initialize_materialize_repo

	git -C "${MATERIALIZE_TREE}" remote set-url origin "${TREE}"
	git -C "${MATERIALIZE_TREE}" fetch --quiet origin
	git -C "${MATERIALIZE_TREE}" checkout -q --detach "${TREE_HEAD}"
	git -C "${MATERIALIZE_TREE}" reset --quiet --hard "${TREE_HEAD}"
	git -C "${MATERIALIZE_TREE}" clean -fdx -q
	git -C "${MATERIALIZE_TREE}" am --abort >/dev/null 2>&1 || true
}

prepare_worker_tree() {
	if [[ ! -d "${WORKER_TREE}/.git" ]]; then
		log "creating cached worker tree: ${WORKER_TREE}"
		clone_local_repo "${REMOTE_GIT}" "${WORKER_TREE}"
	fi

	git -C "${WORKER_TREE}" remote set-url origin "${REMOTE_GIT}"
	git -C "${WORKER_TREE}" fetch --quiet --prune origin
	git -C "${WORKER_TREE}" checkout -q -B "${BRANCH_NAME}" "origin/${BRANCH_NAME}"
	git -C "${WORKER_TREE}" reset --quiet --hard "origin/${BRANCH_NAME}"
}

copy_untracked_files() {
	python3 - "${TREE}" "${MATERIALIZE_TREE}" <<'PY'
import os
import shutil
import subprocess
import sys

src_root, dst_root = sys.argv[1:3]

proc = subprocess.run(
    ['git', '-C', src_root, 'ls-files', '--others', '--exclude-standard', '-z'],
    check=True,
    capture_output=True,
)

def remove_path(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)

for entry in proc.stdout.split(b'\0'):
    if not entry:
        continue

    rel = entry.decode('utf-8', 'surrogateescape')
    src = os.path.join(src_root, rel)
    dst = os.path.join(dst_root, rel)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if os.path.lexists(dst):
        remove_path(dst)

    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    elif os.path.isfile(src):
        shutil.copy2(src, dst, follow_symlinks=False)
    else:
        raise RuntimeError(f"unsupported untracked path type: {src}")
PY
}

commit_materialized_if_needed() {
	local message="$1"

	git -C "${MATERIALIZE_TREE}" add -A
	if git -C "${MATERIALIZE_TREE}" diff --cached --quiet; then
		return 1
	fi

	git -C "${MATERIALIZE_TREE}" \
		-c commit.gpgSign=false \
		-c core.hooksPath=/dev/null \
		-c user.name='local-vmksft' \
		-c user.email='local-vmksft@nipa.local' \
		commit --quiet -m "${message}"
	return 0
}

patch_has_diff() {
	grep -qE '^(diff --git |---$|Index: )' "$1"
}

patch_is_mail() {
	grep -qE '^From [0-9a-f]{40} ' "$1" && grep -q '^Subject: ' "$1"
}

collect_patch_files() {
	mapfile -t PATCH_FILES < <(
		find "${PATCH_DIR}" -maxdepth 1 -type f \
			\( -name '*.patch' -o -name '*.mbox' \) \
			| LC_ALL=C sort
	)
	(( ${#PATCH_FILES[@]} > 0 )) ||
		die "no .patch or .mbox files found under ${PATCH_DIR}"
	PATCH_COUNT="${#PATCH_FILES[@]}"
}

apply_patch_series() {
	local applied=0
	local patch
	local patch_name

	collect_patch_files
	for patch in "${PATCH_FILES[@]}"; do
		patch_name="$(basename "${patch}")"
		if ! patch_has_diff "${patch}"; then
			log "skipping non-diff patch file ${patch_name}"
			continue
		fi

		if patch_is_mail "${patch}"; then
			log "applying mail patch ${patch_name}"
			if ! git -C "${MATERIALIZE_TREE}" \
				-c commit.gpgSign=false \
				-c core.hooksPath=/dev/null \
				am --quiet -3 --keep-cr --whitespace=nowarn "${patch}"; then
				git -C "${MATERIALIZE_TREE}" am --abort >/dev/null 2>&1 || true
				die "failed to apply mail patch: ${patch_name}"
			fi
		else
			log "applying diff patch ${patch_name}"
			git -C "${MATERIALIZE_TREE}" apply --binary --index "${patch}" ||
				die "failed to apply diff patch: ${patch_name}"
			git -C "${MATERIALIZE_TREE}" \
				-c commit.gpgSign=false \
				-c core.hooksPath=/dev/null \
				-c user.name='local-vmksft' \
				-c user.email='local-vmksft@nipa.local' \
				commit --quiet -m "${DEFAULT_PATCH_COMMIT_PREFIX}: ${patch_name}" ||
				die "failed to commit diff patch snapshot: ${patch_name}"
		fi

		applied=$((applied + 1))
	done

	(( applied > 0 )) || die "no patch files with diff content found under ${PATCH_DIR}"
}

prepare_committed_source() {
	BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
	BRANCH_BASE="$(branch_base_for_label)"
	publish_branch_from_repo "${TREE}" "${TREE_HEAD}" "${BRANCH_NAME}"
}

prepare_dirty_source() {
	BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
	BRANCH_BASE="$(branch_base_for_label)"
	DIRTY_PATCH="${RUN_DIR}/dirty-tracked.patch"

	prepare_materialize_repo

	git -C "${TREE}" diff --binary --no-ext-diff HEAD -- > "${DIRTY_PATCH}"
	if [[ -s "${DIRTY_PATCH}" ]]; then
		log "applying tracked dirty changes into materialized tree"
		git -C "${MATERIALIZE_TREE}" apply --binary --index "${DIRTY_PATCH}" ||
			die "failed to apply tracked dirty changes"
	fi

	copy_untracked_files
	if commit_materialized_if_needed "${DEFAULT_DIRTY_COMMIT_MSG}"; then
		log "created dirty snapshot commit"
	else
		log "dirty mode found no staged, unstaged, or untracked changes; testing committed HEAD"
	fi

	publish_branch_from_repo "${MATERIALIZE_TREE}" "$(git -C "${MATERIALIZE_TREE}" rev-parse HEAD)" "${BRANCH_NAME}"
}

prepare_patches_source() {
	BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
	BRANCH_BASE="$(branch_base_for_label)"

	prepare_materialize_repo
	apply_patch_series
	publish_branch_from_repo "${MATERIALIZE_TREE}" "$(git -C "${MATERIALIZE_TREE}" rev-parse HEAD)" "${BRANCH_NAME}"
}

prepare_source_snapshot() {
	initialize_testing_remote

	case "${MODE}" in
	committed)
		prepare_committed_source
		;;
	dirty)
		prepare_dirty_source
		;;
	patches)
		prepare_patches_source
		;;
	*)
		die "unsupported mode: ${MODE}"
		;;
	esac
}

resolve_mode_metadata() {
	[[ -n "${PATCH_COUNT}" ]] || PATCH_COUNT=0

	case "${MODE}" in
	committed)
		BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
		BRANCH_BASE="$(branch_base_for_label)"
		;;
	dirty)
		BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
		BRANCH_BASE="$(branch_base_for_label)"
		;;
	patches)
		BRANCH_NAME="$(branch_name_for_mode "${MODE_LABEL}")"
		BRANCH_BASE="$(branch_base_for_label)"
		collect_patch_files
		;;
	*)
		die "unsupported mode: ${MODE}"
		;;
	esac
}

branch_name_for_mode() {
	local mode="$1"

	printf '%s-%s-%s\n' "${DEFAULT_BRANCH_NAME}" "${mode}" "${RUN_ID}"
}

branch_base_for_label() {
	case "${MODE_LABEL}" in
	committed)
		printf '%s\n' "${TREE_BASE_DISPLAY}"
		;;
	dirty|patches)
		printf '%s\n' "${TREE_HEAD_DISPLAY}"
		;;
	*)
		printf '%s\n' "${TREE_BASE_DISPLAY}"
		;;
	esac
}

print_explain_and_exit() {
	local results_root
	local run_url
	local patch_line

	results_root="${STATE_DIR}/runs/<timestamp>/www/${EXECUTOR_NAME}"
	run_url="http://${PUBLIC_HOST}:${HTTP_PORT}/runs/<timestamp>/${EXECUTOR_NAME}"

	case "${MODE}" in
	committed)
		patch_line="Committed mode uses the exact committed HEAD from ${TREE} and ignores staged, unstaged, and untracked changes."
		;;
	dirty)
		patch_line="Dirty mode starts from committed HEAD, applies git diff HEAD for tracked changes, copies untracked non-ignored files, creates a synthetic local commit under ${MATERIALIZE_TREE}, and publishes that snapshot."
		;;
	patches)
		patch_line="Patches mode starts from committed HEAD, applies ${PATCH_COUNT} patch file(s) from ${PATCH_DIR} in lexical order inside ${MATERIALIZE_TREE}, and publishes the resulting synthetic patched tree."
		;;
	esac

	cat <<EOF
Local vmksft execution plan
===========================

Source tree:
  path: ${SOURCE_TREE_DISPLAY}
  frozen execution tree: ${TREE}
  current branch: ${SOURCE_BRANCH}
  current HEAD: ${TREE_HEAD_DISPLAY}
  base metadata: ${BRANCH_BASE}

Selected mode:
  mode: ${MODE_LABEL}
  published branch name: ${BRANCH_NAME}
  ${patch_line}

NIPA repos and paths:
  cached bare testing remote: ${REMOTE_GIT}
  cached worker tree: ${WORKER_TREE}
  cached materialize tree: ${MATERIALIZE_TREE}
  run state root: ${STATE_DIR}
  stable site root: ${SITE_ROOT}
  per-run results root: ${results_root}
  writable state restriction: all writable harness state stays under ${STATE_DIR}

Branch handoff:
  The wrapper publishes the selected tree into ${REMOTE_GIT} as ${BRANCH_NAME}.
  It then writes branches.json pointing at that published branch.
  The NIPA fetcher fetches remotes in ${WORKER_TREE}, finds ${BRANCH_NAME}, and
  checks it out detached for the actual build/test run.
  The branches.json "base" field is metadata only in the current NIPA fetcher;
  execution is driven by the published branch ref, not by the base field.

 VM / executor settings:
  executor: ${EXECUTOR_NAME}
  target: ${EXECUTOR_TARGET}
  guest cpus: ${GUEST_CPUS}
  guest memory: ${GUEST_MEMORY}
  worker cap: ${THREADS}
  dynamic cpu target: ${DEFAULT_TARGET_CPU_UTIL_PCT}%
  scheduler window: ${DEFAULT_SCHEDULER_HISTORY_SEC}s sampled every ${DEFAULT_SCHEDULER_SAMPLE_PERIOD_SEC}s
  idle VM shutdown: ${DEFAULT_VM_IDLE_SHUTDOWN_SEC}s
  build clean policy: ${BUILD_CLEAN}
  init prompt: ${INIT_PROMPT}
  virtiofsd: ${VIRTIOFSD_PATH:-not found}

HTTP / results publishing:
  internal bind address: ${INTERNAL_HTTP_BIND}
  internal manifest port: ${INTERNAL_HTTP_PORT}
  public site port: ${HTTP_PORT}
  manifest fetch URL: http://127.0.0.1:${INTERNAL_HTTP_PORT}/contest/branches.json
  site base URL: http://${PUBLIC_HOST}:${HTTP_PORT}/
  latest run URL: http://${PUBLIC_HOST}:${HTTP_PORT}/latest/index.html
  published executor base URL: ${run_url}

Build behavior:
  Kernel build reuse is keyed on the checked-out worker tree's git tree object
  plus config inputs and gcov state.
  Identical resulting trees skip the kernel build entirely.
  Changed trees run vng --build incrementally by default.
  Config input changes force make mrproper before vng --build under the current
  "config-change" policy.
  After the kernel phase, the local vmksft wrapper still runs make headers and rebuilds
  selftests for TARGETS=${EXECUTOR_TARGET} in the worker tree.

What this means in practice:
  If ${TREE} is on branch ${SOURCE_BRANCH}, then committed mode tests the
  committed HEAD of that branch.
  Dirty mode tests that branch plus its current staged/unstaged/untracked
  changes.
  Patches mode ignores the dirty working tree and tests the patch directory on
  top of the committed HEAD of ${SOURCE_BRANCH}.
EOF
}

# shellcheck disable=SC2317
cleanup() {
	local rc=$?

	if [[ -n "${SITE_REFRESH_PID}" ]] && kill -0 "${SITE_REFRESH_PID}" 2>/dev/null; then
		kill "${SITE_REFRESH_PID}" 2>/dev/null || true
		wait "${SITE_REFRESH_PID}" 2>/dev/null || true
	fi

	if [[ -n "${HTTP_PID}" ]] && kill -0 "${HTTP_PID}" 2>/dev/null; then
		kill "${HTTP_PID}" 2>/dev/null || true
		wait "${HTTP_PID}" 2>/dev/null || true
	fi

	return "${rc}"
}

# shellcheck disable=SC2317
handle_stop_signal() {
	local sig="$1"

	if (( RUN_COMPLETE == 1 )); then
		log "received ${sig}; stopping HTTP server"
		exit "${RUN_RESULT_RC}"
	fi

	log "received ${sig}; aborting local vmksft run"
	exit 130
}

stage_ui_assets() {
	local destination="$1"
	local asset

	mkdir -p "${destination}/assets"
	cp "${WEB_DIR}/nipa.css" "${destination}/assets/nipa.css"
	cp "${WEB_DIR}/nipa.js" "${destination}/assets/nipa.js"
	cp "${WEB_DIR}/contest.js" "${destination}/assets/contest.js"

	for asset in \
		"favicon-contest.png" \
		"favicon-status.png" \
		"favicon-stats.png" \
		"favicon-flakes.png" \
		"favicon-nic.png"
	do
		cp "${UI_ROOT}/${asset}" "${destination}/${asset}"
	done
}

write_redirect_page() {
	local path="$1"
	local target="$2"
	local title="$3"

	cat > "${path}" <<EOF
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="0; url=${target}">
  <title>${title}</title>
</head>
<body>
  <p>Redirecting to <a href="${target}">${target}</a>.</p>
</body>
</html>
EOF
}

stage_contest_shell() {
	local destination="$1"

	cp "${CONTEST_HTML_TEMPLATE}" "${destination}/contest.html"
	write_redirect_page "${destination}/index.html" "./contest.html" "${EXECUTOR_NAME} result log"
}

stage_run_artifact_links() {
	local config_name

	config_name="$(basename "${CONFIG_PATH}")"
	ln -sfn ../executor.log "${WEB_ROOT}/executor.log"
	ln -sfn ../http-server.log "${WEB_ROOT}/http-server.log"
	ln -sfn "../${config_name}" "${WEB_ROOT}/${config_name}"
}

write_run_metadata() {
	python3 - "${RUN_META_JSON}" \
		"${RUN_ID}" \
		"${EXECUTOR_NAME}" \
		"${EXECUTOR_TARGET}" \
		"${MODE_LABEL}" \
		"${SOURCE_TREE_DISPLAY}" \
		"${SOURCE_BRANCH}" \
		"${TREE_HEAD_DISPLAY}" \
		"${TREE_BASE_DISPLAY}" \
		"${BRANCH_NAME}" \
		"${BRANCH_DATE}" \
		"${PUBLIC_HOST}" \
		"${HTTP_PORT}" \
		"${RUN_PUBLIC_PREFIX}" \
		"${SERVICE_JOB_ID}" \
		"${TREE}" <<'PY'
import json
import sys

path, run_id, executor_name, targets, mode, source_tree, source_branch, source_head, source_base, published_branch, branch_date, public_host, http_port, run_public_prefix, job_id, actual_tree = sys.argv[1:]
data = {
    "run_id": run_id,
    "executor_name": executor_name,
    "targets": targets,
    "mode": mode,
    "source_tree": source_tree,
    "source_branch": source_branch,
    "source_head": source_head,
    "source_base": source_base,
    "actual_tree": actual_tree,
    "published_branch": published_branch,
    "branch_date": branch_date,
    "public_host": public_host,
    "http_port": int(http_port),
    "run_public_prefix": run_public_prefix,
}

if job_id:
    data["job_id"] = job_id

with open(path, "w", encoding="utf-8") as fp:
    json.dump(data, fp, indent=2, sort_keys=True)
PY
}

write_run_dashboard_page() {
	write_redirect_page "${WEB_ROOT}/index.html" "/contest.html?branch=${BRANCH_NAME}" "${EXECUTOR_NAME} run ${RUN_ID}"
	write_redirect_page "${WEB_ROOT}/contest.html" "/contest.html?branch=${BRANCH_NAME}" "${EXECUTOR_NAME} run ${RUN_ID}"
}

write_executor_dashboard_alias() {
	write_redirect_page "${EXECUTOR_INDEX}" "../index.html" "${EXECUTOR_NAME} redirect"
}

write_pending_summary_page() {
	write_redirect_page "${SUMMARY_HTML}" "../index.html" "${EXECUTOR_NAME} summary redirect"
}

write_infra_failure_page() {
	local reason="$1"

	cat > "${SUMMARY_HTML}" <<EOF
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="shortcut icon" href="/favicon-status.png" type="image/png">
  <title>${EXECUTOR_NAME} infrastructure failure</title>
</head>
<body>
  <p>The executor did not produce a readable final summary page.</p>
  <p>Reason: ${reason}</p>
  <p>Open <a href="../index.html">the run view</a>, <a href="../executor.log">executor.log</a>, or <a href="../http-server.log">http-server.log</a>.</p>
</body>
</html>
EOF
}

render_results_page() {
	python3 "${BIN_DIR}/render-vmksft-results.py" \
		--manifest "${MANIFEST_PATH}" \
		--summary-json "${SUMMARY_JSON}" \
		--html "${SUMMARY_HTML}" \
		--executor-name "${EXECUTOR_NAME}" \
		--mode "${MODE}" \
		--targets "${EXECUTOR_TARGET}" \
		--source-tree "${TREE}" \
		--source-branch "${SOURCE_BRANCH}" \
		--source-head "${TREE_HEAD}" \
		--results-manifest-url "./jsons/results.json" \
		--executor-log-url "../executor.log" \
		--http-log-url "../http-server.log" \
		--dashboard-url "../index.html"
}

refresh_site_history() {
	python3 "${BIN_DIR}/build-vmksft-history.py" \
		--state-dir "${STATE_DIR}" \
		--site-root "${SITE_ROOT}" \
		--executor-name "${EXECUTOR_NAME}"
}

start_site_refresh_loop() {
	SITE_REFRESH_LOG="${RUN_DIR}/site-refresh.log"
	: > "${SITE_REFRESH_LOG}"

	(
		while true; do
			if ! refresh_site_history >>"${SITE_REFRESH_LOG}" 2>&1; then
				sleep 1
				continue
			fi
			sleep 2
		done
	) &
	SITE_REFRESH_PID=$!
}

wait_for_manual_shutdown() {
	if (( EXIT_WHEN_DONE == 1 )); then
		log "exit-when-done requested; stopping the built-in HTTP server"
		return 0
	fi

	log "results are still being served at http://${PUBLIC_HOST}:${HTTP_PORT}/"
	log "press Ctrl-C or Ctrl-Z when you are done inspecting the web view"

	while kill -0 "${HTTP_PID}" 2>/dev/null; do
		sleep 1
	done
}

trap cleanup EXIT
trap 'handle_stop_signal INT' INT
trap 'handle_stop_signal TERM' TERM
trap 'handle_stop_signal TSTP' TSTP

while [[ $# -gt 0 ]]; do
	case "$1" in
	--tree)
		require_value "$@"
		TREE="$2"
		shift 2
		;;
	--state-dir)
		require_value "$@"
		STATE_DIR="$2"
		shift 2
		;;
	--mode)
		require_value "$@"
		MODE="$2"
		shift 2
		;;
	--patch-dir)
		require_value "$@"
		PATCH_DIR="$2"
		shift 2
		;;
	--build-clean)
		require_value "$@"
		BUILD_CLEAN="$2"
		shift 2
		;;
	--explain|-explain)
		EXPLAIN_ONLY=1
		shift
		;;
	--threads)
		require_value "$@"
		THREADS="$2"
		shift 2
		;;
	--cpus)
		require_value "$@"
		GUEST_CPUS="$2"
		shift 2
		;;
	--memory)
		require_value "$@"
		GUEST_MEMORY="$2"
		shift 2
		;;
	--init-prompt)
		require_value "$@"
		INIT_PROMPT="$2"
		shift 2
		;;
	--http-port)
		require_value "$@"
		HTTP_PORT="$2"
		shift 2
		;;
	--internal-http-bind)
		require_value "$@"
		INTERNAL_HTTP_BIND="$2"
		shift 2
		;;
	--internal-http-port)
		require_value "$@"
		INTERNAL_HTTP_PORT="$2"
		shift 2
		;;
	--public-host)
		require_value "$@"
		PUBLIC_HOST="$2"
		shift 2
		;;
	--job-meta)
		require_value "$@"
		JOB_META_PATH="$2"
		shift 2
		;;
	--exit-when-done)
		EXIT_WHEN_DONE=1
		shift
		;;
	--fresh-cache)
		FRESH_CACHE=1
		shift
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		die "unknown argument: $1"
		;;
	esac
done

need_cmd git
need_cmd python3
need_cmd vng
need_cmd virtme-run
VIRTIOFSD_PATH="$(find_virtiofsd || true)"
if [[ -n "${VIRTIOFSD_PATH}" ]]; then
	log "using virtiofsd at ${VIRTIOFSD_PATH}"
else
	log "virtiofsd was not found; virtme-ng may fall back to slower 9p sharing"
fi

TREE="$(realpath -e -- "${TREE}")"
STATE_DIR="$(realpath -m -- "${STATE_DIR}")"
if [[ -n "${PATCH_DIR}" ]]; then
	PATCH_DIR="$(realpath -e -- "${PATCH_DIR}")"
fi
if [[ -n "${JOB_META_PATH}" ]]; then
	JOB_META_PATH="$(realpath -e -- "${JOB_META_PATH}")"
fi

if [[ -z "${INTERNAL_HTTP_PORT}" ]]; then
	INTERNAL_HTTP_PORT="${HTTP_PORT}"
fi
MODE_LABEL="${MODE}"

[[ -d "${TREE}/tools/testing/selftests/net" ]] ||
	die "tree does not look like a kernel checkout with selftests/net: ${TREE}"
git -C "${TREE}" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
	die "tree is not a git repository: ${TREE}"
[[ -d "${PATCH_DIR}" || -z "${PATCH_DIR}" ]] || die "--patch-dir is not a directory: ${PATCH_DIR}"
[[ "${HTTP_PORT}" =~ ^[0-9]+$ ]] || die "--http-port must be a non-negative integer: ${HTTP_PORT}"
[[ "${INTERNAL_HTTP_PORT}" =~ ^[0-9]+$ ]] ||
	die "--internal-http-port must be a non-negative integer: ${INTERNAL_HTTP_PORT}"
case "${MODE}" in
committed|dirty|patches)
	;;
*)
	die "--mode must be one of: committed, dirty, patches"
	;;
esac
case "${BUILD_CLEAN}" in
always|never|config-change)
	;;
*)
	die "--build-clean must be one of: always, never, config-change"
	;;
esac
if [[ "${MODE}" == "patches" ]]; then
	[[ -n "${PATCH_DIR}" ]] || die "--patch-dir is required for --mode patches"
else
	[[ -z "${PATCH_DIR}" ]] || die "--patch-dir is only valid with --mode patches"
fi

HOST_CPUS="$(nproc)"
HOST_MEM_KB="$(host_mem_kb)"
HOST_MEM_MIB="$(( HOST_MEM_KB / 1024 ))"
resolve_guest_memory
resolve_guest_cpus
resolve_threads
resolve_scheduler_limits
[[ "${THREADS}" =~ ^[0-9]+$ ]] || die "--threads must resolve to a non-negative integer: ${THREADS}"
SOURCE_BRANCH="$(current_source_branch)"
SOURCE_TREE_DISPLAY="${TREE}"

RUN_ID="$(date -u +%Y%m%d-%H%M%S)-$$"
RUN_DIR="${STATE_DIR}/runs/${RUN_ID}"
CACHE_DIR="${STATE_DIR}/cache"
REMOTE_GIT="${CACHE_DIR}/testing.git"
WORKER_TREE="${CACHE_DIR}/worker-tree"
MATERIALIZE_TREE="${CACHE_DIR}/materialize-tree"
SITE_ROOT="${STATE_DIR}/site"
SITE_RUNS_ROOT="${SITE_ROOT}/runs"
WEB_ROOT="${RUN_DIR}/www"
EXECUTOR_ROOT="${WEB_ROOT}/${EXECUTOR_NAME}"
CONFIG_PATH="${RUN_DIR}/${EXECUTOR_NAME}.ini"
EXECUTOR_INDEX="${EXECUTOR_ROOT}/index.html"
SUMMARY_JSON="${EXECUTOR_ROOT}/summary.json"
MANIFEST_PATH="${EXECUTOR_ROOT}/jsons/results.json"
LIVE_STATUS_JSON="${EXECUTOR_ROOT}/live-status.json"
SUMMARY_HTML="${EXECUTOR_ROOT}/summary.html"
RUN_META_JSON="${WEB_ROOT}/run-meta.json"
RUN_PUBLIC_PREFIX="/runs/${RUN_ID}"

if (( FRESH_CACHE == 1 )); then
	log "dropping cached worker/build/materialize state under ${CACHE_DIR}"
	rm -rf "${CACHE_DIR}"
fi

mkdir -p "${RUN_DIR}" "${CACHE_DIR}" "${SITE_ROOT}/contest" "${SITE_RUNS_ROOT}" "${EXECUTOR_ROOT}/jsons" "${EXECUTOR_ROOT}/results"
ln -sfn "${RUN_DIR}" "${STATE_DIR}/latest"
ln -sfn "${RUN_DIR}/www" "${SITE_RUNS_ROOT}/${RUN_ID}"
ln -sfn "runs/${RUN_ID}" "${SITE_ROOT}/latest"

BRANCH_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TREE_HEAD="$(git -C "${TREE}" rev-parse HEAD)"
TREE_BASE="$(git -C "${TREE}" rev-parse HEAD^ 2>/dev/null || git -C "${TREE}" rev-parse HEAD)"
TREE_HEAD_DISPLAY="${TREE_HEAD}"
TREE_BASE_DISPLAY="${TREE_BASE}"
if [[ -n "${JOB_META_PATH}" ]]; then
	apply_job_meta
fi
[[ -n "${MODE_LABEL}" ]] || MODE_LABEL="${MODE}"
[[ -n "${SOURCE_TREE_DISPLAY}" ]] || SOURCE_TREE_DISPLAY="${TREE}"
[[ -n "${SOURCE_BRANCH}" ]] || SOURCE_BRANCH="$(current_source_branch)"
[[ -n "${TREE_HEAD_DISPLAY}" ]] || TREE_HEAD_DISPLAY="${TREE_HEAD}"
[[ -n "${TREE_BASE_DISPLAY}" ]] || TREE_BASE_DISPLAY="${TREE_BASE}"
resolve_mode_metadata

if (( EXPLAIN_ONLY == 1 )); then
	print_explain_and_exit
	exit 0
fi

prepare_source_snapshot
prepare_worker_tree
stage_ui_assets "${SITE_ROOT}"
stage_contest_shell "${SITE_ROOT}"
stage_run_artifact_links
write_run_metadata
write_run_dashboard_page
write_executor_dashboard_alias
write_pending_summary_page
refresh_site_history
start_site_refresh_loop

log "host capacity: cpus=${HOST_CPUS} mem_mib=${HOST_MEM_MIB}"
log "using source mode: ${MODE_LABEL}"
if [[ "${MODE}" == "patches" ]]; then
	log "using patch directory: ${PATCH_DIR}"
fi
log "using build clean policy: ${BUILD_CLEAN}"
log "using worker settings: worker_cap=${THREADS} guest_cpus=${GUEST_CPUS} guest_memory=${GUEST_MEMORY} target_cpu=${DEFAULT_TARGET_CPU_UTIL_PCT}%"
log "reusing cached worker tree: ${WORKER_TREE}"
log "all writable harness state stays under ${STATE_DIR}"
log "kernel builds will be skipped when tree and config inputs are unchanged"
log "refreshing site history and latest-run metadata every 2s"

cat > "${SITE_ROOT}/contest/branches.json" <<EOF
[
  {
    "branch": "${BRANCH_NAME}",
    "url": "file://${REMOTE_GIT}",
    "date": "${BRANCH_DATE}",
    "base": "${BRANCH_BASE}"
  }
]
EOF

log "starting private HTTP server on ${INTERNAL_HTTP_BIND}:${INTERNAL_HTTP_PORT}"
python3 "${BIN_DIR}/serve-vmksft-http.py" \
	--port "${INTERNAL_HTTP_PORT}" \
	--bind "${INTERNAL_HTTP_BIND}" \
	--directory "${SITE_ROOT}" \
	>"${RUN_DIR}/http-server.log" 2>&1 &
HTTP_PID=$!

for _ in $(seq 1 20); do
	if python3 - "${INTERNAL_HTTP_PORT}" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/contest/branches.json", timeout=1):
    pass
PY
	then
		SERVER_READY=1
		break
	fi
	sleep 0.2
done

(( SERVER_READY == 1 )) || die "loopback HTTP server did not start correctly"

cat > "${CONFIG_PATH}" <<EOF
[life]
single_shot = true

[executor]
name = ${EXECUTOR_NAME}
group = selftests-net
test = ${EXECUTOR_TARGET}
init = force
deadline_minutes = 480

[remote]
branches = http://127.0.0.1:${INTERNAL_HTTP_PORT}/contest/branches.json

[local]
tree_path = ${WORKER_TREE}
base_path = ${EXECUTOR_ROOT}
json_path = jsons
results_path = results
live_status_path = ${LIVE_STATUS_JSON}

[www]
url = http://${PUBLIC_HOST}:${HTTP_PORT}${RUN_PUBLIC_PREFIX}/${EXECUTOR_NAME}

[env]
paths =

[vm]
cpus = ${GUEST_CPUS}
mem = ${GUEST_MEMORY}
boot_timeout = 180
default_timeout = 1800
init_prompt = ${INIT_PROMPT}
# Give the guest a writable copy-on-write view of the worker tree. This lets
# selftests build helpers in-place without mutating the host-side cache.
virtme_opt = --overlay-rwdir,${WORKER_TREE}
build_reuse = true
build_clean = ${BUILD_CLEAN}

[ksft]
target = ${EXECUTOR_TARGET}
nested_tests = on

[cfg]
thread_cnt = ${THREADS}
thread_spawn_delay = ${DEFAULT_THREAD_SPAWN_DELAY}
scheduler_target_cpu_pct = ${DEFAULT_TARGET_CPU_UTIL_PCT}
scheduler_sample_period_sec = ${DEFAULT_SCHEDULER_SAMPLE_PERIOD_SEC}
scheduler_history_sec = ${DEFAULT_SCHEDULER_HISTORY_SEC}
scheduler_up_hysteresis_pct = ${DEFAULT_SCHEDULER_UP_HYSTERESIS_PCT}
scheduler_down_hysteresis_pct = ${DEFAULT_SCHEDULER_DOWN_HYSTERESIS_PCT}
scheduler_min_available_mem_mib = ${SCHEDULER_MIN_AVAILABLE_MEM_MIB}
scheduler_max_dirty_mib = ${SCHEDULER_MAX_DIRTY_MIB}
scheduler_vm_idle_shutdown_sec = ${DEFAULT_VM_IDLE_SHUTDOWN_SEC}
scheduler_min_workers = 0
EOF

log "running local_vmksft_p.py for TARGETS=${EXECUTOR_TARGET}"
set +e
(
	cd "${RUN_DIR}"
	python3 "${LIB_DIR}/local_vmksft_p.py" "${CONFIG_PATH}"
) 2>&1 | tee "${RUN_DIR}/executor.log"
EXECUTOR_RC=${PIPESTATUS[0]}
set -e

if (( EXECUTOR_RC != 0 )); then
	log "local_vmksft_p.py exited with status ${EXECUTOR_RC}; inspecting produced results"
fi

if [[ -f "${MANIFEST_PATH}" ]]; then
	set +e
	render_results_page
	RENDER_RC=$?
	set -e

	if (( RENDER_RC != 0 )); then
		RUN_RESULT_RC=$(( EXECUTOR_RC != 0 ? EXECUTOR_RC : RENDER_RC ))
		write_infra_failure_page "The executor produced JSON output, but the local HTML summary renderer failed. Check executor.log and http-server.log."
	else
		refresh_site_history
		SUMMARY_EXIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], "r", encoding="utf-8"))["exit_code"])' "${SUMMARY_JSON}")"
		if (( EXECUTOR_RC != 0 )); then
			RUN_RESULT_RC="${EXECUTOR_RC}"
		else
			RUN_RESULT_RC="${SUMMARY_EXIT}"
		fi
	fi
else
	RUN_RESULT_RC=$(( EXECUTOR_RC != 0 ? EXECUTOR_RC : 1 ))
	write_infra_failure_page "The executor completed without publishing a results manifest. Check executor.log and http-server.log."
fi

log "run directory: ${RUN_DIR}"
RUN_COMPLETE=1
wait_for_manual_shutdown
exit "${RUN_RESULT_RC}"
