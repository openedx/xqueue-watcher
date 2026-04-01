# Operator Guide

This guide covers installing, configuring, and running xqueue-watcher in production and
development environments.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration layout](#configuration-layout)
- [Configuration reference](#configuration-reference)
  - [xqwatcher.json](#xqwatcherjson)
  - [logging.json](#loggingjson)
  - [xqueue_servers.json](#xqueue_serversjson)
  - [conf.d queue files](#confd-queue-files)
- [Environment variables](#environment-variables)
- [Running xqueue-watcher](#running-xqueue-watcher)
  - [Directly (bare metal / virtualenv)](#directly-bare-metal--virtualenv)
  - [Docker / docker-compose](#docker--docker-compose)
  - [Kubernetes](#kubernetes)
- [Security considerations](#security-considerations)
- [Metrics](#metrics)
- [Logging](#logging)


---

## Prerequisites

- Python 3.10 or newer (for bare-metal installs)
- A running [XQueue](https://github.com/openedx/xqueue) service
- Docker or a Kubernetes cluster (required when using `ContainerGrader`)


## Installation

The recommended way to install dependencies is with [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/openedx/xqueue-watcher.git
cd xqueue-watcher
uv sync
```

Alternatively, with pip:

```bash
pip install -e .
```

The `kubernetes` and `docker` Python packages are optional extras required only by
`ContainerGrader`:

```bash
uv sync --extra kubernetes   # for the Kubernetes backend
uv sync --extra docker       # for the Docker backend
```


## Configuration layout

Keep course-specific files outside the xqueue-watcher repository so you can update
the watcher independently:

```
config/
├── xqwatcher.json          # optional: override manager defaults
├── logging.json            # optional: Python logging dictConfig
├── xqueue_servers.json     # named XQueue server references (keep out of VCS)
└── conf.d/
    ├── my-course.json      # one file per queue (or group of queues)
    └── another-course.json
```

Start xqueue-watcher by pointing it at the `config/` directory:

```bash
python -m xqueue_watcher -d /path/to/config
```

The watcher will:
1. Load `logging.json` (if present) or fall back to stdout logging.
2. Load `xqwatcher.json` (if present) or fall back to defaults.
3. Load `xqueue_servers.json` (if present) for named server references.
4. Load every `*.json` file in `conf.d/` as a queue configuration.


## Configuration reference

### xqwatcher.json

All keys are optional; missing keys fall back to the defaults shown below.

```json
{
    "HTTP_BASIC_AUTH": null,
    "POLL_TIME": 10,
    "REQUESTS_TIMEOUT": 1,
    "POLL_INTERVAL": 1,
    "LOGIN_POLL_INTERVAL": 5,
    "FOLLOW_CLIENT_REDIRECTS": false
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `HTTP_BASIC_AUTH` | `null` | `[username, password]` for HTTP Basic Auth on all outbound requests. |
| `POLL_TIME` | `10` | Seconds between liveness checks of watcher threads. |
| `REQUESTS_TIMEOUT` | `1` | Timeout (seconds) for outbound HTTP requests to XQueue. |
| `POLL_INTERVAL` | `1` | Seconds between queue-poll attempts per watcher thread. |
| `LOGIN_POLL_INTERVAL` | `5` | Seconds between login-retry attempts when authentication fails. |
| `FOLLOW_CLIENT_REDIRECTS` | `false` | Follow HTTP redirects on XQueue requests. |


### logging.json

A standard Python [logging dictConfig](https://docs.python.org/3/library/logging.config.html#logging-config-dictschema)
object.  If this file is absent xqueue-watcher writes structured log lines to stdout
(suitable for container runtimes and Kubernetes).

Example — write to a rotating file:

```json
{
    "version": 1,
    "disable_existing_loggers": false,
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "/var/log/xqueue-watcher/xqueue-watcher.log",
            "maxBytes": 10485760,
            "backupCount": 5,
            "formatter": "standard"
        }
    },
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(process)d [%(name)s] %(filename)s:%(lineno)d - %(message)s"
        }
    },
    "root": {
        "handlers": ["file"],
        "level": "INFO"
    }
}
```

For Kubernetes and container environments, omit `logging.json` entirely and control
the log level with the `XQWATCHER_LOG_LEVEL` environment variable.


### xqueue_servers.json

Defines named XQueue server connections so that credentials are kept in one place and
out of the per-queue conf.d files.  This file should **never be committed to version
control**; in Kubernetes it is injected as a Secret.

```json
{
    "default": {
        "SERVER": "http://xqueue-svc:18040",
        "AUTH": ["lms_user", "s3cr3t"]
    },
    "staging": {
        "SERVER": "http://xqueue-staging:18040",
        "AUTH": ["lms_user", "staging_pass"]
    }
}
```

Each key is a server name that can be referenced from conf.d files using `SERVER_REF`.


### conf.d queue files

Each JSON file in `conf.d/` may configure one or more queues.  A minimal file using a
named server reference:

```json
{
    "course-101-grading": {
        "SERVER_REF": "default",
        "CONNECTIONS": 2,
        "HANDLERS": [
            {
                "HANDLER": "xqueue_watcher.containergrader.ContainerGrader",
                "KWARGS": {
                    "grader_root": "/graders/course-101/",
                    "image": "registry.example.com/course-101-grader:latest",
                    "backend": "kubernetes",
                    "namespace": "grading",
                    "cpu_limit": "500m",
                    "memory_limit": "256Mi",
                    "timeout": 30
                }
            }
        ]
    }
}
```

Alternatively, embed server connection details directly (acceptable in non-secret
environments):

```json
{
    "course-101-grading": {
        "SERVER": "http://xqueue-svc:18040",
        "AUTH": ["lms_user", "s3cr3t"],
        "CONNECTIONS": 1,
        "HANDLERS": [...]
    }
}
```

> **Note**: `SERVER_REF` and `SERVER`/`AUTH` are mutually exclusive.  Providing both
> raises a `ValueError` at startup.

#### Queue configuration keys

| Key | Required | Description |
|-----|----------|-------------|
| `SERVER` | One of `SERVER` or `SERVER_REF` | XQueue server URL, e.g. `http://xqueue:18040`. |
| `AUTH` | With `SERVER` | `[username, password]` for the XQueue Django user. |
| `SERVER_REF` | One of `SERVER` or `SERVER_REF` | Name of a server from `xqueue_servers.json`. |
| `CONNECTIONS` | No (default: 1) | Number of polling threads to spawn for this queue. |
| `HANDLERS` | Yes | List of handler objects (see below). |
| `NAME_OVERRIDE` | No | Poll a different queue name than the config key. |

#### Handler configuration keys

| Key | Required | Description |
|-----|----------|-------------|
| `HANDLER` | Yes | Dotted Python path to a `Grader` subclass, e.g. `xqueue_watcher.containergrader.ContainerGrader`. |
| `KWARGS` | No | Keyword arguments passed to the handler constructor. |
| `CODEJAIL` | No | CodeJail sandbox configuration (legacy; prefer `ContainerGrader`). |

See [Grader Interface](grader-interface.md) for the full list of built-in handlers and
their `KWARGS`.


---

## Environment variables

All environment variables use the `XQWATCHER_` prefix.  They override or supplement
JSON file configuration and are the recommended way to inject settings in containers.

### Manager settings

| Variable | Default | Description |
|----------|---------|-------------|
| `XQWATCHER_LOG_LEVEL` | `INFO` | Root log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `XQWATCHER_HTTP_BASIC_AUTH` | — | HTTP Basic Auth as `username:password`. |
| `XQWATCHER_POLL_TIME` | `10` | Seconds between liveness checks of watcher threads. |
| `XQWATCHER_REQUESTS_TIMEOUT` | `1` | Timeout (seconds) for outbound HTTP requests. |
| `XQWATCHER_POLL_INTERVAL` | `1` | Seconds between queue-poll attempts. |
| `XQWATCHER_LOGIN_POLL_INTERVAL` | `5` | Seconds between login-retry attempts. |
| `XQWATCHER_FOLLOW_CLIENT_REDIRECTS` | `false` | Follow HTTP redirects. |
| `XQWATCHER_VERIFY_TLS` | `true` | Verify TLS certificates. **Never set to `false` in production.** |

### ContainerGrader defaults

These set deployment-wide defaults that individual conf.d queue configs may override.

| Variable | Default | Description |
|----------|---------|-------------|
| `XQWATCHER_GRADER_BACKEND` | `kubernetes` | Container backend: `kubernetes` or `docker`. |
| `XQWATCHER_GRADER_NAMESPACE` | `default` | Kubernetes namespace for grading Jobs. |
| `XQWATCHER_GRADER_CPU_LIMIT` | `500m` | CPU limit for grading containers. |
| `XQWATCHER_GRADER_MEMORY_LIMIT` | `256Mi` | Memory limit for grading containers. |
| `XQWATCHER_GRADER_TIMEOUT` | `20` | Max wall-clock seconds per grading job. |
| `XQWATCHER_DOCKER_HOST_GRADER_ROOT` | — | Host-side absolute path to the grader root when xqueue-watcher itself runs in a Docker container (see [Docker section](#docker--docker-compose)). |
| `XQWATCHER_SUBMISSION_SIZE_LIMIT` | `1048576` | Maximum submission size in bytes (1 MB). Larger submissions are rejected before a container is launched. |


---

## Running xqueue-watcher

### Directly (bare metal / virtualenv)

```bash
# Install dependencies
uv sync        # or: pip install -e .

# Run
python -m xqueue_watcher -d /path/to/config
```

xqueue-watcher runs in the foreground.  Use a process supervisor (systemd, supervisord,
etc.) for production deployments.


### Docker / docker-compose

A `docker-compose.yml` is included for local development.  It starts:
- An XQueue service
- xqueue-watcher (mounts `conf.d/` and `data/` as volumes)
- Builds the `grader-base:local` image that your grader images extend

Before starting, set the host-side path to your grader data:

```bash
export XQWATCHER_DOCKER_HOST_GRADER_ROOT="$(pwd)/data"
docker compose up
```

> **Why `XQWATCHER_DOCKER_HOST_GRADER_ROOT`?**  When xqueue-watcher runs inside a
> container and spawns grader containers via the Docker socket, the Docker daemon
> interprets bind-mount source paths relative to the *host* filesystem — not the
> watcher container.  This variable tells the watcher what the corresponding host-side
> path is so it can pass the correct path to the Docker daemon.

To build your own grader image for testing:

```bash
docker build -f grader_support/Dockerfile.base -t grader-base:local .
# Then build your course-specific grader image on top of grader-base:local
```

See [Course Team Guide — Local Testing](course-teams.md#local-testing-without-edx) for
a worked example.


### Kubernetes

Manifests are provided in `deploy/kubernetes/`:

```
deploy/kubernetes/
├── kustomization.yaml
├── configmap.yaml       # conf.d queue configs
├── secret.yaml.template # xqueue_servers.json (fill in credentials)
├── deployment.yaml      # xqueue-watcher Deployment
├── serviceaccount.yaml  # ServiceAccount for the watcher pod
├── rbac.yaml            # RBAC for creating/watching grading Jobs
└── networkpolicy.yaml   # Egress restriction for grading pods
```

**Quickstart:**

1. Copy `deploy/kubernetes/secret.yaml.template` to `secret.yaml`, fill in your XQueue
   credentials, and apply it:

   ```bash
   cp deploy/kubernetes/secret.yaml.template deploy/kubernetes/secret.yaml
   # Edit secret.yaml — add SERVER and AUTH values
   kubectl apply -f deploy/kubernetes/secret.yaml
   ```

2. Edit `deploy/kubernetes/configmap.yaml` to add your queue configurations.

3. Apply the remaining manifests:

   ```bash
   kubectl apply -k deploy/kubernetes/
   ```

**Security posture for grading Jobs:**

The `ContainerGrader` Kubernetes backend applies a defence-in-depth approach to grading
containers:

- Non-root user (UID 1000), read-only root filesystem
- All Linux capabilities dropped
- RuntimeDefault seccomp profile
- `/tmp` backed by a size-capped `emptyDir` (prevents disk exhaustion)
- No service-account token auto-mounted
- CPU and memory limits enforced via Job spec

Operators should additionally ensure:

- The grading namespace enforces the Kubernetes [restricted Pod Security Standard](https://kubernetes.io/docs/concepts/security/pod-security-standards/).
- A `NetworkPolicy` (provided in `deploy/kubernetes/networkpolicy.yaml`) prevents egress
  from grading pods.
- Grader images are signed and scanned; use digest-pinned references in production.
- The TTL controller (`--feature-gates=TTLAfterFinished=true`) is enabled so completed
  grading Jobs are cleaned up automatically.
- PID limits are enforced via a namespace `LimitRange` or `--pod-pids-limit` on the
  kubelet (the Job spec alone cannot set PID limits).

**Using `poll_image_digest` for automatic image updates:**

If you push new grader images to a tag (e.g. `:latest`) and want Kubernetes nodes to
always use the most recent version without relying on `imagePullPolicy: Always` for
every pod, enable digest polling:

```json
{
    "KWARGS": {
        "image": "registry.example.com/course-grader:latest",
        "poll_image_digest": true,
        "digest_poll_interval": 300
    }
}
```

This starts a background thread that periodically resolves the tag to its current digest
(`repo@sha256:…`). Grading Jobs use the pinned digest reference, ensuring nodes pull
the correct image exactly once.


---

## Security considerations

- **Never commit `xqueue_servers.json`** to version control. It contains XQueue
  credentials.  Use a Kubernetes Secret or an equivalent secrets-management system.
- **Use `SERVER_REF`** in conf.d queue files rather than inline `SERVER`/`AUTH` so
  queue configs are safe to commit.
- **Pin grader images by digest** in production (`repo@sha256:…`) to prevent supply-chain
  attacks via mutable tags.
- **Apply the `NetworkPolicy`** in `deploy/kubernetes/networkpolicy.yaml` to prevent
  student code from making outbound network requests during grading.
- **Set `XQWATCHER_VERIFY_TLS=true`** (the default) in all environments.  The `false`
  value exists solely for development with self-signed certificates.


---

## Metrics

xqueue-watcher exposes OpenTelemetry metrics via the `xqueue_watcher.metrics` module.
The following instruments are recorded:

| Instrument | Type | Description |
|------------|------|-------------|
| `xqwatcher.process_item` | Counter | Submissions received for grading. |
| `xqwatcher.replies` | Counter | Successful replies sent back to XQueue. |
| `xqwatcher.grader_payload_errors` | Counter | Submissions with unparseable grader payloads. |
| `xqwatcher.grading_time` | Histogram | Wall-clock grading time in seconds. |

Configure an OTLP exporter by setting the standard `OTEL_EXPORTER_OTLP_ENDPOINT`
environment variable before starting xqueue-watcher.


---

## Logging

xqueue-watcher uses Python's standard `logging` module.  The root logger name is
`xqueue_watcher`.

**Without `logging.json`** (default for containers): structured lines are written to
stdout with the format:

```
2024-01-15 12:00:00,123 INFO 42 [xqueue_watcher.manager] manager.py:176 - Starting <XQueueClientThread ...>
```

The level defaults to `INFO` and can be raised or lowered with `XQWATCHER_LOG_LEVEL`.

**With `logging.json`**: the file is loaded as a Python logging
[dictConfig](https://docs.python.org/3/library/logging.config.html#logging-config-dictschema),
giving full control over handlers, formatters, and per-logger levels.

**Debug grader containers**: set `GRADER_DEBUG=1` in the environment of grading
containers to print step-by-step trace output to stderr.  Kubernetes captures both
stdout and stderr in pod logs, so `kubectl logs <pod>` will show both the JSON result
and the debug trace.
