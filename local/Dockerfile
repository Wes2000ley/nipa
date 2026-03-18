FROM fedora:44 AS iproute-build

ARG IPROUTE2_VERSION=6.19.0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN dnf -y --setopt=install_weak_deps=False --nodocs install \
        ca-certificates \
        curl-minimal \
        gcc \
        make \
        bison \
        flex \
        pkgconf-pkg-config \
        iptables-devel \
        libbpf-devel \
        libcap-devel \
        elfutils-libelf-devel \
        libmnl-devel \
        libselinux-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

WORKDIR /tmp

RUN curl -fsSLO "https://www.kernel.org/pub/linux/utils/net/iproute2/iproute2-${IPROUTE2_VERSION}.tar.xz" \
    && tar -xf "iproute2-${IPROUTE2_VERSION}.tar.xz" \
    && make -C "iproute2-${IPROUTE2_VERSION}" -j"$(nproc)" \
    && make -C "iproute2-${IPROUTE2_VERSION}" install \
        DESTDIR=/tmp/iproute2-root \
        PREFIX=/usr \
        SBINDIR=/usr/bin \
        BINDIR=/usr/bin


FROM fedora:44

ARG IPROUTE2_VERSION=6.19.0
ARG VIRTME_NG_VERSION=1.40
ARG PATATT_VERSION=0.7.0
ARG PYLINT_VERSION=4.0.5
ARG RUFF_VERSION=0.15.6

ENV VIRTUAL_ENV=/opt/nipa-venv
ENV PATH="${VIRTUAL_ENV}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN dnf -y --setopt=install_weak_deps=False --nodocs install \
        bash \
        bc \
        bison \
        bpftool \
        bridge-utils \
        ca-certificates \
        clang \
        conntrack-tools \
        coreutils \
        cpio \
        curl-minimal \
        diffutils \
        dropwatch \
        dwarves \
        elfutils-libelf-devel \
        ethtool \
        ebtables-legacy \
        findutils \
        flex \
        gawk \
        gcc \
        gcc-c++ \
        git \
        git-email \
        gzip \
        iperf3 \
        iputils \
        iptables \
        iptables-utils \
        ipv6toolkit \
        ipvsadm \
        jq \
        kmod \
        lld \
        linuxptp \
        llvm \
        make \
        net-tools \
        netperf \
        ndisc6 \
        nmap-ncat \
        netsniff-ng \
        nftables \
        numactl-devel \
        openvswitch \
        openssl-devel \
        patch \
        packetdrill \
        packetdrill-data \
        perl \
        perf \
        procps-ng \
        psmisc \
        python3 \
        python3-pip \
        python3-psutil \
        python3-pyyaml \
        python3-requests \
        python-unversioned-command \
        qemu-img \
        qemu-kvm \
        rsync \
        ShellCheck \
        socat \
        systemd-udev \
        tar \
        tcpdump \
        teamd \
        traceroute \
        util-linux \
        virtiofsd \
        wireshark-cli \
        which \
        xz \
        yamllint \
    && dnf clean all \
    && rm -rf /var/cache/dnf

COPY --from=iproute-build /tmp/iproute2-root/usr/bin/ /usr/bin/
COPY --from=iproute-build /tmp/iproute2-root/usr/lib/ /usr/lib/
COPY --from=iproute-build /tmp/iproute2-root/usr/share/ /usr/share/

RUN python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir --upgrade pip setuptools wheel \
    && "${VIRTUAL_ENV}/bin/pip" install --no-cache-dir \
        "virtme-ng==${VIRTME_NG_VERSION}" \
        "patatt==${PATATT_VERSION}" \
        "pylint==${PYLINT_VERSION}" \
        "ruff==${RUFF_VERSION}" \
        "requests" \
        "psutil" \
        "PyYAML" \
    && git config --system --add safe.directory '*'

WORKDIR /workspace/nipa/local

CMD ["/bin/bash"]
