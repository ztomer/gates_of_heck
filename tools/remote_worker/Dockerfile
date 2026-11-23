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

# The toolchain is the pinned one even where a checkout's rust-toolchain.toml says `stable`: that
# file would send rustup to download a toolchain into a read-only RUSTUP_HOME with no network.
ENV RUSTUP_TOOLCHAIN=${RUST_VERSION}

# The versions this image claims, for `goh remote-shard`'s parity check.
RUN { python3 -V; python3 -m pip list --format=freeze; rustc -V; shellcheck -V | sed -n 2p; } > /opt/goh-worker-versions.txt

WORKDIR /work
CMD ["sleep", "infinity"]
