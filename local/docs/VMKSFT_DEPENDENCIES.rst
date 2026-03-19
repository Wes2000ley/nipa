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

These are required by the Docker Compose service and its per-job execution
path:

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

The service-side job execution path checks the critical toolchain directly and
will fail the queued job early if they are missing.

Python modules used by the local and shared executor code:

- ``python3-psutil``

Recommended install:

.. code-block:: bash

  sudo apt install \
    git python3 python3-psutil \
    make rsync qemu-system-x86 qemu-utils virtiofsd

``virtme-ng`` is commonly installed via pip locally:

.. code-block:: bash

  python3 -m pip install --user virtme-ng


Kernel And Selftest Build Requirements
--------------------------------------

These are the base packages you want for building the kernel and the kselftest
target set used by the local vmksft service:

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
- ``packetdrill``
- ``iptables``
- ``iptables-utils``
  Provides ``nfbpf_compile`` used by packet filter setup helpers.
- ``nftables``
- ``conntrack``
- ``tcpdump``
- ``iputils-arping``
- ``traceroute``
- ``bpftool``
- ``perf``
- ``ipvsadm``
- ``openvswitch``
- ``psmisc``
  Provides helpers like ``pkill`` used by some networking tests.
- ``kmod``
  Provides ``modprobe`` / ``lsmod`` for tests that check module state.
- ``wireshark-cli``
  Provides ``tshark`` for drop-monitor decoding.
- ``dropwatch``
  Provides ``dwdump`` for drop-monitor decoding.
- ``ndisc6``
- ``ipv6toolkit``
  Provides ``ra6`` for several IPv6 route advertisement tests.
- ``linuxptp``
  Provides ``ptp4l``, ``phc2sys``, and ``phc_ctl`` used by TSN tests.
- ``teamd``
- ``systemd-udev``
  Provides ``udevadm``.
- ``python-unversioned-command``
  Useful for tests that still invoke ``python`` rather than ``python3``.

Recommended install:

.. code-block:: bash

  sudo apt install \
    iproute2 ethtool jq iperf3 iputils-ping iputils-arping netcat-openbsd \
    socat python3-yaml netsniff-ng packetdrill iptables nftables \
    conntrack tcpdump traceroute bpftool linux-perf ipvsadm openvswitch-switch \
    psmisc kmod tshark ndisc6 teamd

Some remaining helpers are packaged differently across distros:

- ``ra6`` comes from ``ipv6toolkit`` on Fedora.
- ``nfbpf_compile`` comes from ``iptables-utils`` on Fedora.
- ``dwdump`` comes from ``dropwatch`` on Fedora.
- the container image installs these explicitly so the Compose workflow does
  not depend on the host distro naming.

Notes:

- ``iperf3`` was directly required by ``udpgro-fwd.sh``.
- ``jq`` is used by multiple ``drivers/net/netdevsim`` tests and other JSON
  parsing helpers.
- ``packetdrill`` is required by the default ``net/packetdrill`` target in the
  local vmksft harness. The container image builds ``packetdrill`` from the
  upstream Google repository rather than using the distro RPM so newer AccECN
  grammar support is available.
- ``iptables``, ``nftables``, and ``conntrack`` are used throughout
  ``netfilter/``.
- ``tcpdump`` is directly checked by multiple tests including
  ``broadcast_ether_dst.sh``, ``arp_ndisc_untracked_subnets.sh``, and
  ``drop_monitor_tests.sh``.
- ``bpftool`` is required by ``bpf_offload.py``.
- ``perf`` is checked by ``fib_tests.sh`` and ``openvswitch/openvswitch.sh``.
- ``openvswitch`` tools are used by ``openvswitch/openvswitch.sh`` and several
  PMTU test paths.
- ``ndisc6`` and ``ra6`` are required by IPv6 bridge and FIB tests.
- ``tshark`` and ``dwdump`` are required by ``drop_monitor_tests.sh``.
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
    iputils-arping iptables nftables conntrack tcpdump traceroute bpftool \
    linux-perf ipvsadm openvswitch-switch psmisc kmod libcap-dev libelf-dev \
    libnuma-dev libssl-dev make netcat-openbsd ndisc6 netsniff-ng \
    packetdrill pahole python3 python3-psutil python3-yaml \
    qemu-system-x86 qemu-utils rsync socat teamd tshark virtiofsd

Then add any distro-specific extras you care about, such as ``ra6``,
``nfbpf_compile``, and ``dwdump``, using the package names from your host
distribution.

Then install ``virtme-ng``:

.. code-block:: bash

  python3 -m pip install --user virtme-ng

Then replace the stock ``iproute2`` with a newer build if you want closer
cloud parity for the failing networking tests.
