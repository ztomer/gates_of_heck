# goh's remote test worker (GOH.REMOTEWORKERS, owner 2026-11-22): the Linux-safe
# part of a gates_of_heck test run, sent here from the Mac by `goh remote-shard`.
#
# Owner ruling: install NOTHING on the host. Every tool the suite needs is in
# this image, at the Mac's exact versions; the tree, caches and cargo target
# live on the bind mount (/work), so a rebuilt image keeps them.
#
# Every version is a NAMED release, never a moving tag (house rule 2026-10-06),
# and every download is checked against its sha256. A tool goh's manifest
# versions (gates/required_tools.tsv, the ONE place a tool version is written)
# is a build arg with NO default here: `goh remote-shard` reads each from the
# manifest (shellcheck's checksum from .github/actions/goh-floors/
# shellcheck.sha256) and tags the image by this file plus those args, so a
# raised pin is a new image. It compares the built versions with the manifest at
# run time and runs the shard locally on any difference.
# tests/test_remote_shard.py holds the pinning contract.
#
# python 3.15.2 on Debian trixie, slim; the digest is the multi-arch index read
# from Docker Hub on 2026-11-22.
FROM python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ARG PYTEST_VERSION
ARG PYTEST_XDIST_VERSION
ARG PYTEST_COV_VERSION
ARG PYTEST_ASYNCIO_VERSION=1.4.0
ARG PLUGGY_VERSION
ARG EXECNET_VERSION
ARG PILLOW_VERSION
ARG NUMPY_VERSION
ARG COVERAGE_VERSION
ARG RUFF_VERSION
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_SHA256=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
ARG RUST_VERSION=1.100.0
ARG SHELLCHECK_VERSION
ARG SHELLCHECK_SHA256
ARG CARGO_LLVM_COV_VERSION
ARG CARGO_LLVM_COV_SHA256=6272d16a61570f053580272527150135a91e5a2b9e4f0291c66d1e5309b5cacc
# sccache at the Mac's release; the checksum is the release's own .sha256 asset, which GitHub's
# asset digest matched (read 2026-11-22).
ARG SCCACHE_VERSION=0.19.0
ARG SCCACHE_SHA256=aa73c7270c997894038c0d8f0d297865f57569e1687883322bc431713d4bb9dd
# GOH.WORKERFAILS: the tools the suite meets that the manifest names without an exact pin, each at
# the Mac's release (read 2026-11-23); the checksums are GitHub's asset digests (git-cliff,
# cargo-machete) and node's SHASUMS256.txt, each matched by a download.
ARG GIT_CLIFF_VERSION=2.14.2
ARG GIT_CLIFF_SHA256=fa21e2fc70046fcfd0e43e5e4613873ab799aff3d9dc8559c00d31b270e417d0
ARG CARGO_MACHETE_VERSION=0.10.1
ARG CARGO_MACHETE_SHA256=785d381985140ffa172246d82089adeb7f2eb9c774d961c48f77a4494023afb5
ARG NODE_VERSION=26.13.1
ARG NODE_SHA256=5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864

# git: the suite builds throwaway repos. gcc/libc6-dev: cargo links. curl, xz,
# ca-certificates: the downloads below. Debian's own packages, from the
# snapshot the pinned base image carries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git gcc libc6-dev curl xz-utils ca-certificates make jq procps \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir \
      "pytest==${PYTEST_VERSION}" "pytest-xdist==${PYTEST_XDIST_VERSION}" \
      "pytest-cov==${PYTEST_COV_VERSION}" "pytest-asyncio==${PYTEST_ASYNCIO_VERSION}" \
      "pluggy==${PLUGGY_VERSION}" "execnet==${EXECNET_VERSION}" \
      "pillow==${PILLOW_VERSION}" "numpy==${NUMPY_VERSION}" \
      "coverage==${COVERAGE_VERSION}" "ruff==${RUFF_VERSION}"

# Rust: rustup at a named release, the toolchain at the Mac's stable, read-only
# for the worker (RUSTUP_HOME/CARGO_HOME here; the worker's registry cache is
# on /work through its own CARGO_HOME, which `goh remote-shard` sets on `docker run`).
ENV RUSTUP_HOME=/opt/rustup \
    PATH=/opt/cargo/bin:$PATH
RUN curl -fsSLo /tmp/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
 && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
 && chmod +x /tmp/rustup-init \
 && CARGO_HOME=/opt/cargo /tmp/rustup-init -y --no-modify-path --profile minimal \
      --default-toolchain "${RUST_VERSION}" -c rustfmt -c clippy -c llvm-tools \
 && rm /tmp/rustup-init \
 && chmod -R a+rX /opt/rustup /opt/cargo

RUN curl -fsSLo /tmp/sc.tar.gz "https://github.com/koalaman/shellcheck/releases/download/v${SHELLCHECK_VERSION}/shellcheck-v${SHELLCHECK_VERSION}.linux.x86_64.tar.gz" \
 && echo "${SHELLCHECK_SHA256}  /tmp/sc.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/sc.tar.gz -C /tmp \
 && install -m 0755 "/tmp/shellcheck-v${SHELLCHECK_VERSION}/shellcheck" /usr/local/bin/shellcheck \
 && rm -rf /tmp/sc.tar.gz "/tmp/shellcheck-v${SHELLCHECK_VERSION}"

RUN curl -fsSLo /tmp/llvmcov.tgz "https://github.com/taiki-e/cargo-llvm-cov/releases/download/v${CARGO_LLVM_COV_VERSION}/cargo-llvm-cov-x86_64-unknown-linux-gnu.tar.gz" \
 && echo "${CARGO_LLVM_COV_SHA256}  /tmp/llvmcov.tgz" | sha256sum -c - \
 && tar -xzf /tmp/llvmcov.tgz -C /opt/cargo/bin \
 && rm /tmp/llvmcov.tgz

# sccache (GOH.REMOTEEVICT): the worker's cargo builds reuse compiled crates across runs and lane
# slots. cargo runs goh's own wrapper ahead of it, as on the Mac (lib/goh-rustc, GOH.SCCACHEENV:
# cargo's variables out of the key), from the slot's synced tree: `goh remote-shard` sets
# RUSTC_WRAPPER on each run's `docker exec`. Its cache is $HOME/.cache/sccache on the
# mount, capped by its own LRU at SCCACHE_CACHE_SIZE; lib/worker_evict.py counts it in the
# worker's 80 GB and never sweeps it.
RUN curl -fsSLo /tmp/sccache.tgz "https://github.com/mozilla/sccache/releases/download/v${SCCACHE_VERSION}/sccache-v${SCCACHE_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${SCCACHE_SHA256}  /tmp/sccache.tgz" | sha256sum -c - \
 && tar -xzf /tmp/sccache.tgz -C /tmp \
 && install -m 0755 "/tmp/sccache-v${SCCACHE_VERSION}-x86_64-unknown-linux-musl/sccache" /usr/local/bin/sccache \
 && rm -rf /tmp/sccache.tgz "/tmp/sccache-v${SCCACHE_VERSION}-x86_64-unknown-linux-musl"
ENV SCCACHE_CACHE_SIZE=20G

# git-cliff (tools/pytest.sh's preflight, goh ship's next version), cargo-machete (the rust gate's
# unused-dependency step) and node (checks/check_js_syntax.py's engine): GOH.WORKERFAILS.
RUN curl -fsSLo /tmp/cliff.tgz "https://github.com/orhun/git-cliff/releases/download/v${GIT_CLIFF_VERSION}/git-cliff-${GIT_CLIFF_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${GIT_CLIFF_SHA256}  /tmp/cliff.tgz" | sha256sum -c - \
 && tar -xzf /tmp/cliff.tgz -C /tmp \
 && install -m 0755 "/tmp/git-cliff-${GIT_CLIFF_VERSION}/git-cliff" /usr/local/bin/git-cliff \
 && rm -rf /tmp/cliff.tgz "/tmp/git-cliff-${GIT_CLIFF_VERSION}"
RUN curl -fsSLo /tmp/machete.tgz "https://github.com/bnjbvr/cargo-machete/releases/download/v${CARGO_MACHETE_VERSION}/cargo-machete-v${CARGO_MACHETE_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${CARGO_MACHETE_SHA256}  /tmp/machete.tgz" | sha256sum -c - \
 && tar -xzf /tmp/machete.tgz -C /tmp \
 && install -m 0755 "/tmp/cargo-machete-v${CARGO_MACHETE_VERSION}-x86_64-unknown-linux-musl/cargo-machete" /usr/local/bin/cargo-machete \
 && rm -rf /tmp/machete.tgz "/tmp/cargo-machete-v${CARGO_MACHETE_VERSION}-x86_64-unknown-linux-musl"
RUN curl -fsSLo /tmp/node.txz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  /tmp/node.txz" | sha256sum -c - \
 && tar -xJf /tmp/node.txz -C /tmp "node-v${NODE_VERSION}-linux-x64/bin/node" \
 && install -m 0755 "/tmp/node-v${NODE_VERSION}-linux-x64/bin/node" /usr/local/bin/node \
 && rm -rf /tmp/node.txz "/tmp/node-v${NODE_VERSION}-linux-x64"

# The run's uid has a name and a home (GOH.WORKERFAILS): with no passwd entry, `Path.home()` under a
# test's dropped HOME raised and pytest's basetemp read `pytest-of-unknown`. HOME is the mount's, as
# `goh remote-shard` sets it on every run (GOH_REMOTE_USER's default, 1000:1000).
RUN groupadd -g 1000 goh && useradd -u 1000 -g 1000 -M -d /work/cache/home -s /bin/bash goh

# The toolchain is the pinned one even where a checkout's rust-toolchain.toml says `stable`: that
# file would send rustup to download a toolchain into a read-only RUSTUP_HOME with no network.
ENV RUSTUP_TOOLCHAIN=${RUST_VERSION}

# The versions this image claims, for `goh remote-shard`'s parity check.
RUN { python3 -V; python3 -m pip list --format=freeze; rustc -V; shellcheck -V | sed -n 2p; sccache --version; git-cliff --version; echo "cargo-machete $(cargo-machete --version)"; echo "node $(node --version)"; } > /opt/goh-worker-versions.txt

WORKDIR /work
CMD ["sleep", "infinity"]
