ARG UV_VERSION=0.10.7
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update && \
    apt-get install -y --no-install-recommends git-core && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m --shell /bin/false app

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /edx/app/xqueue_watcher

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . /edx/app/xqueue_watcher
RUN uv sync --frozen --no-dev
# Note: the `codejail` optional extra (edx-codejail) is intentionally omitted
# from this image.  In the Kubernetes deployment, student code runs inside an
# isolated container (ContainerGrader) — the container boundary provides the
# sandbox via Linux namespaces, cgroups, capability dropping, network isolation,
# and a read-only filesystem.  codejail (AppArmor + OS-level user-switching)
# requires host-level AppArmor configuration that is unavailable inside
# Kubernetes pods and adds no meaningful security benefit on top of container
# isolation.  Install the `codejail` extra only when running the legacy
# JailedGrader on a bare-metal or VM host with AppArmor configured.

# Put the venv on PATH so `xqueue-watcher` and any other installed scripts are
# available without a symlink.
ENV PATH="/edx/app/xqueue_watcher/.venv/bin:$PATH"

USER app

CMD ["xqueue-watcher", "-d", "/etc/xqueue-watcher"]

FROM base AS edx.org
USER app
CMD ["xqueue-watcher", "-d", "/etc/xqueue-watcher"]
