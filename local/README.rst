Local vmksft Harness
====================

This directory contains the Docker-Compose-backed local vmksft service.

There is one supported way to run it:

- start the long-lived ``vmksft-service`` container
- submit jobs through ``local/vmksft``
- browse results from the service-owned site root under ``local/state/vmksft-net/site``

Native-host deployment, the legacy one-shot runner, and dual-backend wrapper
behavior are not supported.


Layout
------

The ``local/`` tree is split by subsystem:

- ``bin/`` for service entrypoints and helper scripts
- ``lib/`` for reusable Python implementation code
- ``web/`` for the local contest UI assets and templates
- ``tests/`` for the local unit tests
- ``docs/`` for supporting documentation such as dependency notes
- ``systemd/`` for the Compose-managed systemd sample
- ``vmksft`` as the supported user-facing submission wrapper


Quick Start
-----------

From the repo root:

.. code-block:: bash

  cd ~/nipa/local
  cp .env.example .env
  docker compose build
  docker compose up -d vmksft-service

Submit jobs through the wrapper:

.. code-block:: bash

  ./vmksft committed
  ./vmksft dirty
  ./vmksft committed --tests 'net:tcp_fastopen_backup_key.sh'
  ./vmksft committed --inject-file /tmp/helper.sh /abs/kernel/tree/tools/testing/selftests/net/
  ./vmksft dirty --inject-file /tmp/a.c /abs/kernel/tree/net/core/a.c --inject-file /tmp/b.h /abs/kernel/tree/include/linux/b.h
  ./vmksft patches --patch-dir /path/to/series
  ./vmksft patches --patch-dir /path/to/series --tests 'net/packetdrill:tcp_rcv_toobig.pkt'
  ./vmksft patches --patch-dir /path/to/series --inject-file ./fix.c /abs/kernel/tree/net/core/fix.c
  ./vmksft list
  ./vmksft show --follow JOB_ID

The wrapper requires the Compose service to already be running. It executes all
queue operations inside the container and stages patch directories into the
container before submission when needed.


Supported Operation
-------------------

The service executes queued jobs strictly one at a time. Each queued job runs
in its own child process, but the only supported parent runtime is the
long-lived ``vmksft-service`` container.

- ``committed`` freezes the current committed ``HEAD`` of the configured kernel tree
- ``dirty`` freezes tracked and untracked working-tree content into a synthetic snapshot
- ``patches`` freezes the supplied ``.patch`` / ``.mbox`` directory at queue time
- ``--inject-file SRC DEST`` overlays a source file onto an absolute destination path under the configured kernel tree
- repeat ``--inject-file`` once per file you want to overlay
- if ``DEST`` ends with ``/``, vmksft appends the source basename after normalizing the path
- ``--tests`` restricts a job to explicit selectors such as ``prog.sh`` or ``target:prog.sh``
- ``show --follow`` prints job JSON updates until the job becomes ``complete``, ``failed``, or ``cancelled``

Queued jobs do not drift after submission. A later edit to the kernel tree,
patch folder, injected source file, configured target suite, skip list, or
``--tests`` selector does not change the queued payload.

The service publishes a stable browse root at ``http://localhost:8888/`` by
default. The key paths are:

- ``/contest.html`` for the result log
- ``/history.json`` for generated run history
- ``/latest/`` for the most recent run
- ``/runs/<run-id>/`` for stable per-run URLs
- ``/service/status.json`` and ``/service/jobs.json`` for queue status


Runtime State
-------------

All service state lives under ``local/state/vmksft-net/`` by default.

Important subdirectories:

- ``service/jobs/<job-id>/`` stores frozen job snapshots and metadata
- ``service/queue/`` stores queued job pointers
- ``service/running/``, ``service/complete/``, ``service/failed/``, and ``service/cancelled/`` track job state
- ``cache/worker-tree/`` holds the reusable worker checkout and build cache
- ``cache/test-runtime.json`` stores observed per-test runtimes for future queue ordering
- ``runs/<run-id>/`` holds run-scoped logs, config, and published web artifacts
- ``site/`` is the stable docroot served by the Compose service

The worker tree cache is reused across jobs. Kernel build reuse remains keyed
on the checked-out tree object plus config inputs and GCOV state, so unchanged
trees can skip rebuilding while changed trees continue to use incremental
``vng --build`` behavior under the default ``config-change`` policy.
The executor also keeps a small runtime history cache and uses it to front-load
longer tests in later runs. Cached runtimes under 10 seconds are treated as
effectively equal so short tests keep their natural order.


Resource Model
--------------

The Compose service auto-sizes the local executor conservatively for the host:

- guest CPU count defaults to ``2``
- guest memory defaults to ``2G``
- worker count is derived from host memory unless explicitly overridden at submit time

The local executor follows the upstream ``vmksft-p`` per-test launcher shape
and keeps the dynamic host-pressure scheduler on top of that for worker
admission and VM recycling.


Systemd
-------

If you want systemd to keep the supported Compose service running, use:

- ``local/systemd/nipa-vmksft-compose.service.sample``

Replace ``#NIPA#`` with the absolute repo path, then enable the resulting unit.


Notes
-----

- The container provides the userland stack. The host still needs working KVM
  and the same virtualization support the tests depend on.
- ``NIPA_KERNEL_TREE`` in ``local/.env`` must point at the kernel checkout the
  service will snapshot from.
- ``NIPA_STATE_DIR`` in ``local/.env`` may be changed if you want the state on
  a different host path.
- The public HTTP port and published host come from ``NIPA_WEB_PORT`` and
  ``NIPA_PUBLIC_HOST``.
- ``NIPA_VMKSFT_TARGETS`` configures the default whitespace-separated kselftest
  ``TARGETS`` suite for new jobs. The value is frozen into each queued job.
- ``NIPA_VMKSFT_SKIP_TESTS`` may contain a whitespace/comma-separated list of
  test names to suppress from every queued job. The value is frozen into each
  job at submission time.
- The supported user path is ``local/vmksft``.
