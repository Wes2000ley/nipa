Local vmksft Dependencies
=========================

This file is the local ``vmksft-net`` dependency and parity checklist.
It is split into:

- base harness requirements needed to run the wrapper at all
- build requirements needed to build the kernel and kselftests
- userspace tools that the selected net-focused target set expects
- version-sensitive tools where "installed" is not enough and cloud parity
  depends on using a new enough build

The list is intentionally biased toward Ubuntu 24.04 because that is the host
environment currently being used locally.


Base Harness Requirements
-------------------------

These are required by the local wrapper itself:

- ``bash``
- ``git``
- ``python3``
- ``make``
- ``rsync``
- ``vng`` from ``virtme-ng``
- ``virtme-run`` from ``virtme-ng``
- ``qemu-system-x86``
- ``qemu-utils``
- ``virtiofsd`` is strongly recommended

The wrapper checks some of these directly in
``local/run-vmksft-net.sh`` and will refuse to start without them.

Python modules used by the local and shared executor code:

- ``python3-requests``
- ``python3-psutil``

Recommended install:

.. code-block:: bash

  sudo apt install \
    git python3 python3-requests python3-psutil \
    make rsync qemu-system-x86 qemu-utils virtiofsd

``virtme-ng`` is commonly installed via pip locally:

.. code-block:: bash

  python3 -m pip install --user virtme-ng


Kernel And Selftest Build Requirements
--------------------------------------

These are the base packages you want for building the kernel and the kselftest
target set used by ``run-vmksft-net.sh``:

- ``gcc``
- ``bc``
- ``flex``
- ``bison``
- ``libelf-dev``
- ``libssl-dev``
- ``pahole``

Recommended install:

.. code-block:: bash

  sudo apt install \
    gcc bc flex bison libelf-dev libssl-dev pahole

Optional but useful:

- ``lcov`` if you enable GCOV and want coverage capture from the executor


Proven Required Build Headers
-----------------------------

These were directly proven missing by the March 15, 2026 local runs:

- ``libcap-dev``
  Used by ``epoll_busy_poll`` via ``sys/capability.h``.
- ``libnuma-dev``
  Used by ``reuseport_bpf_numa`` via ``numa.h``.

Recommended install:

.. code-block:: bash

  sudo apt install libcap-dev libnuma-dev


Userspace Tools Expected By The Target Set
------------------------------------------

These are either used by the harness itself or by the selected net kselftests
often enough that they should be considered part of the local baseline:

- ``iproute2``
  Provides ``ip``, ``tc``, ``devlink``, ``bridge``, ``ss``, ``nstat``,
  ``lnstat`` and related tools.
- ``ethtool``
- ``jq``
- ``iperf3``
- ``iputils-ping``
- ``netcat-openbsd``
- ``socat``
- ``python3-yaml``
- ``netsniff-ng``

Recommended install:

.. code-block:: bash

  sudo apt install \
    iproute2 ethtool jq iperf3 iputils-ping netcat-openbsd socat \
    python3-yaml netsniff-ng

Notes:

- ``iperf3`` was directly required by ``udpgro-fwd.sh``.
- ``jq`` is used by multiple ``drivers/net/netdevsim`` tests and other JSON
  parsing helpers.
- ``iproute2`` is the largest single userspace dependency for this target set.


Version-Sensitive Parity Requirements
-------------------------------------

For several failures, the issue is not "missing package" but "installed
version is too old compared to cloud".

Current local host:

- ``iproute2 6.1.0``

That version is already proven too old for parts of the selected suite:

- ``fib-rule-tests-sh``
  Reported ``iproute2 iprule too old, missing dscp match`` and
  ``missing flowlabel match``.
- ``devlink-sh``
  Reported ``Unknown option "tc-bw"``.
- ``tc-flower-cfm-sh``
  Reported unsupported ``protocol cfm`` handling.
- ``vxlan-reserved-sh``
  Reported ``vxlan: unknown command "reserved_bits"?``.
- ``router-mpath-seed-sh``
  Showed nexthop-related behavior consistent with older userspace support.

For cloud parity, the local runner needs a newer ``iproute2`` stack than the
Ubuntu 24.04 stock package. That means a newer build of:

- ``ip``
- ``tc``
- ``devlink``
- ``bridge``
- ``ss``
- ``nstat``
- ``lnstat``

Practical guidance:

- treat distro ``iproute2 6.1.0`` as insufficient for this target set
- install or build a newer ``iproute2`` and make sure that newer toolchain is
  what the guest sees in ``PATH``


Things That Are Not Package Problems
------------------------------------

These came from the local runs but are not fixed by simply installing more
packages:

- ``netlink-dumps`` / ``tun`` missing ``ynl.h``
  This was a local executor invocation divergence. The local runner must use
  the same per-test launcher shape as upstream ``vmksft-p``.
- ``custom-multipath-hash-sh``, ``gre-custom-multipath-hash-sh``,
  ``ip6gre-custom-multipath-hash-sh``
  The remaining failures are narrow IPv6 flowlabel-balance behavior
  differences, not a missing host package.
- ``netcons-*`` failures
  These point at local netconsole receive-path behavior, not a missing package.


Recommended Ubuntu 24.04 Baseline
---------------------------------

If you want one baseline install command for the local harness, start here:

.. code-block:: bash

  sudo apt install \
    bc bison ethtool flex gcc git iperf3 iproute2 iputils-ping jq \
    libcap-dev libelf-dev libnuma-dev libssl-dev make netcat-openbsd \
    netsniff-ng pahole python3 python3-psutil python3-requests \
    python3-yaml qemu-system-x86 qemu-utils rsync socat virtiofsd

Then install ``virtme-ng``:

.. code-block:: bash

  python3 -m pip install --user virtme-ng

Then replace the stock ``iproute2`` with a newer build if you want closer
cloud parity for the failing networking tests.
