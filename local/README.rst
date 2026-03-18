Local vmksft Harness
====================

This directory contains local wrappers for exercising NIPA executor code paths
without needing the full netdev cloud deployment.

The first wrapper here, ``run-vmksft-net.sh``, drives a local vmksft shim
against a broad net-focused target set while leaving the upstream executor core
in ``contest/remote/`` close to origin. It is a single-shot local harness
which:

- maintains a cached local testing remote and worker tree under
  ``local/state/vmksft-net/cache/``
- materializes dirty-tree and patch-folder snapshots under the same local cache
  instead of writing back into the source tree
- generates a local ``branches.json`` manifest
- serves that manifest and the resulting JSON artifacts over HTTP on port
  ``8888`` by default
- keeps the HTTP server alive after completion so you can continue browsing the
  generated pages until you stop it with ``Ctrl-C`` or ``Ctrl-Z``; pass
  ``--exit-when-done`` to skip that manual wait
- runs ``local/local_vmksft_p.py`` with a generated config and the cached
  worker tree

This is still narrower than the full cloud matrix, but it now covers a much
larger local netdev slice, including the main ``net`` target plus buckets such
as ``net/af_unix``, ``net/can``, ``net/forwarding``, ``net/mptcp``,
``net/netfilter``, ``net/openvswitch``, ``net/ovpn``, ``net/packetdrill``,
``net/tcp_ao``, ``nci``, and the software-oriented ``drivers/net/*`` suites
used by this harness.

Usage
-----

From the kernel tree:

.. code-block:: bash

  ~/nipa/local/run-vmksft-net.sh

Or point it at another tree:

.. code-block:: bash

  ~/nipa/local/run-vmksft-net.sh --tree /path/to/linux

Common knobs:

- ``--mode committed|dirty|patches`` selects the source input:

  - ``committed`` tests the current committed ``HEAD`` of ``--tree``
  - ``dirty`` tests a synthetic snapshot of the current working tree, including
    staged, unstaged, deleted, and untracked non-ignored files
  - ``patches`` applies a directory of ``.patch`` or ``.mbox`` files on top of
    the current committed ``HEAD``

- ``--patch-dir PATH`` points ``--mode patches`` at a patch directory
- ``--build-clean always|never|config-change`` controls whether tree changes
  trigger a full clean rebuild, no clean rebuild, or a clean rebuild only when
  the config inputs change
- ``--explain`` prints the fully resolved branch/base/cache/build plan and exits
- ``--threads N|auto`` controls the local vmksft worker cap
- ``--cpus N|auto`` controls guest CPU count
- ``--memory SIZE|auto`` controls guest RAM
- ``--init-prompt PROMPT`` overrides the initial guest prompt if virtme-ng
  differs from the default on this host
- ``--http-port N`` changes the public port embedded in generated result URLs
- ``--internal-http-bind HOST`` changes the bind address of the runner's
  private manifest server
- ``--internal-http-port N`` changes the private manifest port used by the
  runner's built-in HTTP server
- ``--public-host HOST`` changes the hostname or IP published in result URLs
- ``--exit-when-done`` makes the wrapper exit after the run instead of keeping
  the built-in HTTP server alive for manual browsing
- ``--fresh-cache`` drops the cached remote and worker tree before the run

Containerized workflow
----------------------

``local/Dockerfile`` and ``local/docker-compose.yml`` package the userspace side
of the vmksft harness in a Fedora 44 image. The container provides the QEMU,
``virtme-ng``, Python, and tooling stack; it does **not** replace the host
kernel. KVM still comes from the host kernel and device access, so the host
must support the same virtualization features that the native wrapper needs.

The image intentionally matches the newer local userspace stack more closely:
it pins ``virtme-ng 1.40``, ``patatt 0.7.0``, ``pylint 4.0.5``, and overlays
``iproute2 6.19.0`` on top of the Fedora base packages. It also installs
the broader net selftest toolchain that the current tree actually checks for:
``packetdrill``, ``iptables``/``ip6tables``, ``nft``, ``conntrack``,
``tcpdump``, ``arping``, ``bpftool``, ``perf``, ``openvswitch``,
``ipvsadm``, ``mausezahn``, ``ndisc6``, ``ra6``, ``nfbpf_compile``,
``tshark``, ``dwdump``, ``teamd``, ``ptp4l``, ``phc2sys``, and related
helpers. ``sendip`` is not packaged in Fedora 44, but the relevant selftest
already has a ``socat`` fallback and the image includes that path.

The current image trim keeps the same test coverage and convenience tools, but
it avoids some duplicate or broader-than-needed packages. The QEMU layer keeps
the x86 system emulator plus the curses UI instead of the larger desktop UI
stack, and the Python modules used by the local scripts come from the dedicated
virtualenv rather than from duplicate Fedora RPMs.

From the repo root, change into ``local/``, copy ``.env.example`` to ``.env``,
edit the paths you want, then build and start the long-lived service:

.. code-block:: bash

  cd ~/nipa/local
  cp .env.example .env
  docker compose build
  docker compose up -d vmksft-service

Submit work through the queued service:

.. code-block:: bash

  docker compose exec vmksft-service \
    python3 /workspace/nipa/local/vmksft_queue.py submit --mode committed

  docker compose exec vmksft-service \
    python3 /workspace/nipa/local/vmksft_queue.py list

The long-lived container serves ``local/state/vmksft-net/site`` on
``http://localhost:8888/`` by default and executes queued jobs strictly one at
a time.

The legacy one-shot runner is still available as the optional Compose profile
``legacy`` via the ``vmksft-runner`` service, but it is now for manual
debugging rather than the default workflow.

By default, Compose bind-mounts ``local/state/`` exactly like the native local
workflow. If you want the artifacts somewhere else on the host, set
``NIPA_STATE_DIR`` in ``local/.env`` to another host path; inside the container
it is still exposed as ``/workspace/nipa/local/state`` so the NIPA paths do not
change.

Runtime state is kept under ``local/state/vmksft-net/`` and each run gets its
own timestamped subdirectory. The ``latest`` symlink is updated to the most
recent run for quick inspection. The cached worker tree keeps kernel build
artifacts between runs, and the local NIPA fork reuses that build when the
worker tree is still at the same file tree with the same config inputs. This is
important for the ``dirty`` and ``patches`` modes, because they create
synthetic commits under ``local/state/``; identical reruns still hit the same
tree-based cache key. The default local policy is ``config-change``, which lets
ordinary source edits use incremental ``vng --build`` rebuilds and only forces
``make mrproper`` when the config inputs change. Use ``--build-clean always`` if
you want the stricter CI-style clean build behavior on every tree change.

The queue service keeps its own control files under
``local/state/vmksft-net/service/``. The important subdirectories are:

- ``jobs/<job-id>/`` for frozen source snapshots plus immutable job metadata
- ``queue/`` for queued job pointers
- ``running/``, ``complete/``, ``failed/``, and ``cancelled/`` for state
  transitions

Queued jobs freeze their source input at submission time. ``committed`` jobs
capture the exact current HEAD, ``dirty`` jobs capture the working tree plus
untracked files into a synthetic committed snapshot, and ``patches`` jobs copy
and apply the current patch directory into a frozen snapshot. This means queued
jobs do not drift if the source tree or patch directory changes before they run.

On larger hosts the default resource sizing is intentionally aggressive. The
wrapper auto-sizes guest CPU count and guest RAM, and it derives the automatic
worker cap from host memory rather than from a fixed CPU formula. The local
executor now follows the upstream ``vmksft-p`` thread-start behavior much more
closely: it does the same up-front selftests build, then starts worker VMs with
the same load-wait hook and per-test ``make ... TEST_PROGS=... run_tests``
launcher shape as the remote executor. The local dynamic scheduler still stays
in place on top of that to manage host pressure; it governs when workers are
admitted or recycled locally, but it does not change the individual test/build
commands. Override the auto values explicitly if you want a smaller or larger
footprint.

After the executor starts, the wrapper serves a stable site root under
``local/state/vmksft-net/site/`` instead of exposing only the current run's
``www/`` tree. The site root has:

- ``/contest.html`` as the main local result log using the shared contest UI
- ``/index.html`` as a redirect to ``/contest.html``
- ``/history.json`` as the generated run history metadata
- ``/contest/all-results.json`` and ``/contest/filters.json`` as the local
  contest UI data sources
- ``/latest/`` as a symlink to the most recent run
- ``/runs/<run-id>/`` as stable URLs for prior runs
- ``/service/status.json`` and ``/service/jobs.json`` as the long-lived queue
  status endpoints

Each run keeps its own redirecting dashboard at ``/runs/<run-id>/index.html``.
That URL lands in the shared contest log with the run's branch preselected.
The rendered final summary URL at ``/runs/<run-id>/vmksft-net-local/summary.html``
also redirects back to the same run-scoped contest view, while ``summary.json``
continues to expose the structured final totals and metadata.

The Python summary builder still writes ``summary.json`` from the executor's
final detail JSON, but the HTML side is now just a redirect shell rather than a
separate local summary UI.

The wrapper uses a small custom local HTTP server so the raw extensionless log
files under ``results/`` are served as inline text instead of being treated as
downloads by the browser.

Systemd samples
---------------

Two sample unit files live under ``local/systemd/``:

- ``nipa-vmksft-compose.service.sample`` manages the Docker Compose service
- ``nipa-vmksft-native.service.sample`` runs the same long-lived daemon
  directly on the host

Both samples expect you to replace ``#NIPA#`` with the absolute repo path. The
native sample also expects ``#USER#`` to be replaced with the account that owns
the repo and the state directory. Both use ``local/.env`` as the shared config
source. Native submissions use the same queue CLI directly:

.. code-block:: bash

  python3 ~/nipa/local/vmksft_queue.py submit --mode dirty

Notes
-----

- The wrapper intentionally runs the local vmksft shim from a non-git working
  directory. This avoids an odd branch lookup shortcut in NIPA's fetcher and
  forces the executor to resolve the branch from the worker clone's remotes.
- The one-shot wrapper now accepts any absolute ``--state-dir``. The long-lived
  service still keeps all of its writable data under the configured
  ``NIPA_STATE_DIR`` parent.
- The default initial prompt is ``#`` on purpose. The guest prompt seen before
  NIPA resets ``PS1`` includes a dynamic hostname and working directory, so a
  suffix match is more robust than hardcoding the full prompt string.
- The HTTP server listens on all interfaces so other machines can browse the
  generated results. The executor still fetches the manifest locally via
  ``127.0.0.1`` to avoid depending on external name resolution.
- The runner still starts a built-in HTTP server during execution so the
  fetcher can consume ``branches.json`` locally. Under the long-lived service
  that server is private to the job and uses a loopback-only port chosen by the
  daemon, while the daemon itself owns the stable public browse port.
- The worker tree cache is there to avoid unnecessary rebuilds on reruns. Use
  ``--fresh-cache`` if the cache gets wedged or you want to force a fully clean
  local reproduction.
- ``--mode patches`` accepts both mail-style ``git format-patch`` output and
  plain diff-style ``.patch`` files. Non-diff files, such as a cover letter,
  are skipped.
- Generated runtime state is ignored by git via ``local/state/`` in the repo
  root ``.gitignore``.
- Host and userspace prerequisites are listed in
  ``local/VMKSFT_DEPENDENCIES.rst``.
