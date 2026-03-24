"""
12-factor / Kubernetes-compatible settings for xqueue-watcher.

All manager configuration values can be supplied via environment variables
using the ``XQWATCHER_`` prefix.  This module mirrors the keys defined in
:data:`xqueue_watcher.settings.MANAGER_CONFIG_DEFAULTS` so it can be used
as a drop-in source of configuration alongside or instead of the JSON file
read by :func:`xqueue_watcher.settings.get_manager_config_values`.

It also provides :func:`configure_logging`, which initialises a structured
stdout logging configuration without requiring a ``logging.json`` file —
suitable for Kubernetes and any 12-factor environment where logs are consumed
from stdout by the container runtime.

Environment variables
---------------------
XQWATCHER_LOG_LEVEL
    Root log level (default: ``INFO``).  Accepts any standard Python level
    name: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
XQWATCHER_HTTP_BASIC_AUTH
    HTTP Basic Auth credentials as ``username:password``.  Parsed into a
    ``(username, password)`` tuple suitable for ``HTTPBasicAuth(*value)``.
    Unset or empty means no authentication (equivalent to ``None``).
XQWATCHER_POLL_TIME
    Seconds between liveness checks of client threads (integer, default 10).
XQWATCHER_REQUESTS_TIMEOUT
    Timeout in seconds for outbound HTTP requests (integer, default 1).
XQWATCHER_POLL_INTERVAL
    Seconds between queue-polling attempts (integer, default 1).
XQWATCHER_LOGIN_POLL_INTERVAL
    Seconds between login-retry attempts (integer, default 5).
XQWATCHER_FOLLOW_CLIENT_REDIRECTS
    Follow HTTP redirects when ``true`` or ``1``, ignore otherwise
    (boolean, default false).
XQWATCHER_VERIFY_TLS
    Verify TLS certificates for outbound HTTPS requests when ``true`` or ``1``
    (boolean, default true).  Set to ``false`` only in development environments
    with self-signed certificates.  **Never disable in production.**
XQWATCHER_SUBMISSION_SIZE_LIMIT
    Maximum submission size in bytes (integer, default 1048576 = 1 MB).
    Submissions larger than this value are rejected before a grading container
    is launched.  Prevents etcd object-size overflows and resource-exhaustion
    attacks via very large environment variables.

Named XQueue server references (Kubernetes)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
XQueue server connection details — URL and credentials — are kept in
``xqueue_servers.json`` in the config root.  In Kubernetes this file is
the preferred mechanism for injecting secrets: create a Kubernetes Secret
whose keys are ``xqueue_servers.json`` and mount it as a volume into the
config root directory.  Queue configs in ``conf.d`` then reference servers
by name using the ``SERVER_REF`` field, keeping credentials out of those
files entirely.

Example Kubernetes Secret (``stringData`` form)::

    apiVersion: v1
    kind: Secret
    metadata:
      name: xqueue-servers
    stringData:
      xqueue_servers.json: |
        {
          "default": {
            "SERVER": "http://xqueue-svc:18040",
            "AUTH": ["lms", "s3cr3t"]
          }
        }

Mount it alongside the rest of the config::

    volumes:
      - name: xqueue-servers
        secret:
          secretName: xqueue-servers
    volumeMounts:
      - name: xqueue-servers
        mountPath: /config/xqueue_servers.json
        subPath: xqueue_servers.json

Queue configs in ``conf.d`` can then omit ``SERVER`` and ``AUTH``::

    { "my-queue": { "SERVER_REF": "default", "CONNECTIONS": 1, ... } }

ContainerGrader defaults
~~~~~~~~~~~~~~~~~~~~~~~~
These allow operators to set deployment-wide grader defaults without repeating
them in every conf.d queue JSON file.  Individual queue configs may still
override any of these values in their ``KWARGS`` block.

XQWATCHER_GRADER_BACKEND
    Container backend: ``kubernetes`` (default) or ``docker``.
XQWATCHER_GRADER_NAMESPACE
    Kubernetes namespace in which grading Jobs are created (default:
    ``default``).  Ignored by the Docker backend.
XQWATCHER_GRADER_CPU_LIMIT
    CPU limit for grading containers in Kubernetes / Docker notation
    (default: ``500m``).
XQWATCHER_GRADER_MEMORY_LIMIT
    Memory limit for grading containers, e.g. ``256Mi`` (default: ``256Mi``).
XQWATCHER_GRADER_TIMEOUT
    Maximum wall-clock seconds a grading job may run (integer, default 20).
XQWATCHER_DOCKER_HOST_GRADER_ROOT
    Host-side absolute path that corresponds to ``grader_root`` inside the
    watcher container.  Required when xqueue-watcher itself runs in a
    container with the Docker backend: the Docker daemon interprets
    bind-mount source paths relative to the *host* filesystem, not the
    watcher container, so without this mapping the grader directory will
    not be found.  Example: if ``./data`` is mounted at ``/graders`` in the
    watcher container, set this to the absolute host path of ``./data``
    (e.g. ``/home/user/project/data``).  Unset by default (watcher runs
    directly on the host).
"""

import logging
import logging.config
import os

from .settings import MANAGER_CONFIG_DEFAULTS

_PREFIX = "XQWATCHER_"

_LOG_FORMAT = "%(asctime)s %(levelname)s %(process)d [%(name)s] %(filename)s:%(lineno)d - %(message)s"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        return int(raw)
    return default


def _get_str(name: str, default: str | None) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw if raw else default


def _get_auth(name: str, default):
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    username, _, password = raw.partition(":")
    return (username, password)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """
    Initialise logging to stdout using a level read from the environment.

    This is the 12-factor / Kubernetes alternative to supplying a
    ``logging.json`` file.  All log records are written to ``stdout`` so they
    are captured by the container runtime and forwarded to whatever log
    aggregation system is in use (e.g. Fluentd, Loki, CloudWatch).

    The root log level defaults to ``INFO`` and can be overridden via the
    ``XQWATCHER_LOG_LEVEL`` environment variable.  The ``requests`` and
    ``urllib3`` libraries are pinned to ``WARNING`` to suppress noisy
    HTTP-level debug output.
    """
    level = os.environ.get(f"{_PREFIX}LOG_LEVEL", "INFO").strip().upper()

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": _LOG_FORMAT,
            },
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "standard",
                "level": level,
            },
        },
        "root": {
            "handlers": ["stdout"],
            "level": level,
        },
        "loggers": {
            "requests": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
        },
    })


def get_manager_config_from_env() -> dict:
    """
    Return manager configuration populated from environment variables.

    Values not present in the environment fall back to
    :data:`~xqueue_watcher.settings.MANAGER_CONFIG_DEFAULTS`.
    """
    return {
        "HTTP_BASIC_AUTH": _get_auth(
            f"{_PREFIX}HTTP_BASIC_AUTH",
            MANAGER_CONFIG_DEFAULTS["HTTP_BASIC_AUTH"],
        ),
        "POLL_TIME": _get_int(
            f"{_PREFIX}POLL_TIME",
            MANAGER_CONFIG_DEFAULTS["POLL_TIME"],
        ),
        "REQUESTS_TIMEOUT": _get_int(
            f"{_PREFIX}REQUESTS_TIMEOUT",
            MANAGER_CONFIG_DEFAULTS["REQUESTS_TIMEOUT"],
        ),
        "POLL_INTERVAL": _get_int(
            f"{_PREFIX}POLL_INTERVAL",
            MANAGER_CONFIG_DEFAULTS["POLL_INTERVAL"],
        ),
        "LOGIN_POLL_INTERVAL": _get_int(
            f"{_PREFIX}LOGIN_POLL_INTERVAL",
            MANAGER_CONFIG_DEFAULTS["LOGIN_POLL_INTERVAL"],
        ),
        "FOLLOW_CLIENT_REDIRECTS": _get_bool(
            f"{_PREFIX}FOLLOW_CLIENT_REDIRECTS",
            MANAGER_CONFIG_DEFAULTS["FOLLOW_CLIENT_REDIRECTS"],
        ),
    }


def get_container_grader_defaults() -> dict:
    """
    Return deployment-wide ContainerGrader defaults from environment variables.

    These values are used when a ``ContainerGrader`` is constructed without
    an explicit value for the corresponding parameter.  Any value supplied
    directly in the conf.d ``KWARGS`` block takes precedence.
    """
    return {
        "backend": _get_str(f"{_PREFIX}GRADER_BACKEND", "kubernetes"),
        "namespace": _get_str(f"{_PREFIX}GRADER_NAMESPACE", "default"),
        "cpu_limit": _get_str(f"{_PREFIX}GRADER_CPU_LIMIT", "500m"),
        "memory_limit": _get_str(f"{_PREFIX}GRADER_MEMORY_LIMIT", "256Mi"),
        "timeout": _get_int(f"{_PREFIX}GRADER_TIMEOUT", 20),
        "docker_host_grader_root": _get_str(f"{_PREFIX}DOCKER_HOST_GRADER_ROOT", None),
    }
