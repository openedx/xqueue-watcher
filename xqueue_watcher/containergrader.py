"""
A grader implementation that executes student code inside an isolated container.

Supports two backends:
  - "kubernetes": creates a batch/v1 Job per submission (production)
  - "docker": runs a local Docker container (local dev / CI)

This is the recommended replacement for JailedGrader on Kubernetes deployments.
The Kubernetes backend applies a defence-in-depth security posture:
  - Non-root user (UID 1000), read-only root filesystem
  - All Linux capabilities dropped
  - RuntimeDefault seccomp profile (restricts available syscalls)
  - /tmp emptyDir with a size cap (prevents disk exhaustion)
  - No service-account token auto-mounted
  - CPU and memory limits to prevent resource exhaustion
    (PID limits must be enforced via a namespace LimitRange or kubelet --pod-pids-limit)

Operators should also ensure:
  - The grader namespace enforces the Kubernetes "restricted" Pod Security Standard
  - A NetworkPolicy is applied to prevent egress from grading pods (see deploy/)
  - Grader images are signed and scanned; use digest-pinned references in production
  - The TTL controller is enabled so orphaned Jobs are reaped automatically
"""

import json
import logging
import os
import random
import threading
import time
import uuid
from pathlib import Path

from .grader import Grader
from .env_settings import get_container_grader_defaults


_BACKEND_KUBERNETES = "kubernetes"
_BACKEND_DOCKER = "docker"
_SUPPORTED_BACKENDS = (_BACKEND_KUBERNETES, _BACKEND_DOCKER)

# Maximum submission size (bytes). Submissions larger than this are rejected
# before a container is launched to prevent etcd object-size overflows (K8s
# limit ~1.5 MB) and resource-exhaustion via very large env vars.
_SUBMISSION_SIZE_WARN_BYTES = 32 * 1024   # 32 KB
_SUBMISSION_SIZE_LIMIT_BYTES = int(
    os.environ.get("XQWATCHER_SUBMISSION_SIZE_LIMIT", str(1024 * 1024))  # 1 MB default
)

log = logging.getLogger(__name__)


class ImageDigestPoller:
    """
    Background thread that periodically resolves an image tag to its digest.

    Resolves ``repo:tag`` → ``repo@sha256:…`` by querying the Docker registry
    via the Docker SDK's ``inspect_distribution`` API (no image pull required).
    The resolved reference is cached and refreshed every ``poll_interval`` seconds.

    Thread-safe: ``resolved_image`` may be read from any thread at any time.

    If the initial resolution fails, ``resolved_image`` returns the original
    unresolved reference so that grading can proceed with ``imagePullPolicy:
    Always`` as a safe fallback.
    """

    def __init__(self, image: str, poll_interval: int = 300) -> None:
        self._image = image
        self._poll_interval = poll_interval
        self._resolved: str | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._poll_loop, name=f"digest-poller-{image}", daemon=True
        )
        self._thread.start()

    @property
    def resolved_image(self) -> str:
        with self._lock:
            return self._resolved if self._resolved is not None else self._image

    def _poll_loop(self) -> None:
        while True:
            self._refresh()
            time.sleep(self._poll_interval)

    def _refresh(self) -> None:
        try:
            import docker as docker_sdk

            client = docker_sdk.APIClient()
            info = client.inspect_distribution(self._image)
            digest = info["Descriptor"]["digest"]
            # Strip digest ref (image@sha256:...) then strip tag if present.
            # A tag is the last colon-separated segment that appears after the
            # last slash (so registry ports like registry:5000/... are preserved).
            base = self._image.split("@")[0]
            last_colon = base.rfind(":")
            last_slash = base.rfind("/")
            repo = base[:last_colon] if last_colon > last_slash else base
            resolved = f"{repo}@{digest}"
            with self._lock:
                if self._resolved != resolved:
                    log.info(
                        "Resolved grader image %s → %s", self._image, resolved
                    )
                    self._resolved = resolved
        except Exception:
            log.warning(
                "Failed to resolve digest for grader image %s; "
                "will retry in %ds",
                self._image,
                self._poll_interval,
                exc_info=True,
            )


class ContainerGrader(Grader):
    """
    Grades student submissions by running them inside an isolated container.

    The grader scripts and staff answer are baked into the course-specific grader
    image.  The container runs the complete grading pipeline (preprocessing, running
    both the staff answer and the student submission, comparing results) and returns
    a JSON grade result.  The watcher pod does not need local access to grader files.

    Configuration (passed as KWARGS in the conf.d JSON handler config):

      grader_root        - Path to the grader directory inside the container image.
                           For the Docker backend this is bind-mounted from the host;
                           for Kubernetes the scripts are baked into the image.
      image              - Docker image to run. Should extend grader-base and include
                           all course-specific grader scripts and dependencies.
      backend            - "kubernetes" or "docker". Defaults to
                           XQWATCHER_GRADER_BACKEND env var, or "kubernetes".
      namespace          - Kubernetes namespace to create Jobs in. Defaults to
                           XQWATCHER_GRADER_NAMESPACE env var, or "default".
      cpu_limit          - CPU limit for the grading container. Defaults to
                           XQWATCHER_GRADER_CPU_LIMIT env var, or "500m".
      memory_limit       - Memory limit for the grading container. Defaults to
                           XQWATCHER_GRADER_MEMORY_LIMIT env var, or "256Mi".
      timeout            - Maximum wall-clock seconds a grading job may run. Defaults
                           to XQWATCHER_GRADER_TIMEOUT env var, or 20.
      docker_host_grader_root - Host-side absolute path corresponding to grader_root
                           inside the watcher container.  Required when xqueue-watcher
                           itself runs in a container (e.g. via docker-compose with the
                           Docker socket mounted): the Docker daemon interprets
                           bind-mount sources relative to the *host* filesystem, so
                           without this mapping the grader directory will not be found.
                           Example: if ``./data`` is mounted at ``/graders`` in the
                           watcher container, set this to the absolute host path of
                           ``./data``.  Defaults to XQWATCHER_DOCKER_HOST_GRADER_ROOT
                           env var, or None (watcher runs directly on the host).
      image_pull_policy  - Kubernetes imagePullPolicy for grading Jobs: "Always",
                           "IfNotPresent", or "Never". When None (default) the policy
                           is inferred from the image reference: "IfNotPresent" for
                           digest-pinned refs (``repo@sha256:…``), "Always" for
                           tag-based refs (no digest present).
      poll_image_digest  - When True and ``image`` is a tag-based reference, start
                           a background ``ImageDigestPoller`` that periodically
                           resolves the tag to its current digest.  Grading Jobs will
                           use the most recently resolved ``repo@digest`` reference,
                           which ensures Kubernetes nodes always pull the latest
                           pushed image without relying on ``imagePullPolicy: Always``
                           for every pod. Default: False.
      digest_poll_interval - Seconds between digest resolution polls when
                           ``poll_image_digest`` is True. Default: 300.
    """

    def __init__(
        self,
        grader_root,
        image,
        backend=None,
        namespace=None,
        cpu_limit=None,
        memory_limit=None,
        timeout=None,
        image_pull_policy=None,
        poll_image_digest=False,
        digest_poll_interval=300,
        docker_host_grader_root=None,
        **kwargs,
    ):
        env_defaults = get_container_grader_defaults()
        resolved_backend = backend if backend is not None else env_defaults["backend"]
        if resolved_backend not in _SUPPORTED_BACKENDS:
            raise ValueError(
                f"Unsupported backend {resolved_backend!r}. Choose from {_SUPPORTED_BACKENDS}."
            )
        super().__init__(grader_root=grader_root, fork_per_item=False, **kwargs)
        self.image = image
        self.backend = resolved_backend
        self.namespace = namespace if namespace is not None else env_defaults["namespace"]
        self.cpu_limit = cpu_limit if cpu_limit is not None else env_defaults["cpu_limit"]
        self.memory_limit = memory_limit if memory_limit is not None else env_defaults["memory_limit"]
        self.timeout = timeout if timeout is not None else env_defaults["timeout"]
        self.docker_host_grader_root = (
            docker_host_grader_root
            if docker_host_grader_root is not None
            else env_defaults["docker_host_grader_root"]
        )

        # image_pull_policy: explicit override or auto-detect from image ref.
        # Normalise to title-case ("Always", "IfNotPresent", "Never") regardless
        # of how the value was supplied in KWARGS — the Kubernetes API is
        # case-sensitive and rejects variants like "always" or "ALWAYS".
        _policy_map = {p.lower(): p for p in ("Always", "IfNotPresent", "Never")}
        if image_pull_policy is not None:
            self.image_pull_policy = _policy_map.get(
                image_pull_policy.strip().lower(), image_pull_policy.strip()
            )
        elif "@sha256:" in image:
            self.image_pull_policy = "IfNotPresent"
        else:
            self.image_pull_policy = "Always"

        # Optional background digest polling for tag-based image references.
        self._digest_poller: ImageDigestPoller | None = None
        if poll_image_digest and "@sha256:" not in image:
            self._digest_poller = ImageDigestPoller(
                image=image, poll_interval=digest_poll_interval
            )
            log.info(
                "Started digest poller for grader image %s (interval=%ds)",
                image,
                digest_poll_interval,
            )

        # Lazily-initialised Kubernetes API clients (created once per instance
        # on first use to avoid per-submission config-load overhead).
        self._k8s_lock = threading.Lock()
        self._k8s_batch_v1 = None
        self._k8s_core_v1 = None

    def _effective_image(self) -> str:
        """Return the image reference to use for container execution.

        If a digest poller is active and has resolved a digest, returns the
        pinned ``repo@sha256:…`` form.  Falls back to the configured tag-based
        reference otherwise.
        """
        if self._digest_poller is not None:
            return self._digest_poller.resolved_image
        return self.image

    # ------------------------------------------------------------------
    # Internal: container execution
    # ------------------------------------------------------------------

    def _get_k8s_clients(self):
        """Return cached (batch_v1, core_v1) Kubernetes API clients.

        Config is loaded and clients are constructed once per instance the
        first time this method is called.  Subsequent calls return the cached
        objects, avoiding repeated kubeconfig reads on every submission.
        """
        if self._k8s_batch_v1 is not None:
            return self._k8s_batch_v1, self._k8s_core_v1
        with self._k8s_lock:
            if self._k8s_batch_v1 is None:
                try:
                    from kubernetes import client as k8s_client, config as k8s_config
                except ImportError:
                    raise RuntimeError(
                        "The 'kubernetes' package is required for the kubernetes backend. "
                        "Install it with: uv add kubernetes"
                    )
                try:
                    k8s_config.load_incluster_config()
                except k8s_config.ConfigException:
                    k8s_config.load_kube_config()
                self._k8s_batch_v1 = k8s_client.BatchV1Api()
                self._k8s_core_v1 = k8s_client.CoreV1Api()
        return self._k8s_batch_v1, self._k8s_core_v1

    def _run(self, grader_path, code, seed, grader_config=None):
        """
        Run the complete grading pipeline inside a container.

        The container entrypoint (grader_support.entrypoint) handles:
          - Loading the grader module (baked into the image)
          - Preprocessing both staff answer and student submission
          - Running both through grader_support.run
          - Comparing results and returning the final grade JSON

        Returns the raw stdout bytes (JSON grade result).
        Raises RuntimeError on timeout or non-zero exit.
        """
        # Enforce submission size limits. Very large submissions passed as env
        # vars contribute to the Pod object stored in etcd (~1.5 MB limit), and
        # can be used for resource-exhaustion attacks.
        code_bytes = len(code.encode("utf-8"))
        if code_bytes > _SUBMISSION_SIZE_LIMIT_BYTES:
            raise ValueError(
                f"Submission too large ({code_bytes} bytes). "
                f"Maximum allowed size is {_SUBMISSION_SIZE_LIMIT_BYTES} bytes."
            )
        if code_bytes > _SUBMISSION_SIZE_WARN_BYTES:
            self.log.warning(
                "Submission code is large (%d bytes). Very large submissions may "
                "exceed Kubernetes API object size limits when passed via env var.",
                code_bytes,
            )
        if grader_config is None:
            grader_config = {}
        if self.backend == _BACKEND_KUBERNETES:
            return self._run_kubernetes(grader_path, code, seed, grader_config)
        return self._run_docker(grader_path, code, seed, grader_config)

    def _run_kubernetes(self, grader_path, code, seed, grader_config):
        """Create a Kubernetes Job, wait for it, collect stdout, delete it."""
        from kubernetes import client as k8s_client  # noqa: F401 — needed for V1DeleteOptions

        batch_v1, core_v1 = self._get_k8s_clients()

        job_name = f"xqueue-grader-{uuid.uuid4().hex[:12]}"

        job_manifest = self._build_k8s_job(job_name, grader_path, code, seed, grader_config)

        try:
            batch_v1.create_namespaced_job(namespace=self.namespace, body=job_manifest)
            self.log.debug("Created Job %s", job_name)

            stdout = self._wait_and_collect_k8s(
                batch_v1, core_v1, job_name, timeout=self.timeout
            )
            return stdout
        finally:
            try:
                batch_v1.delete_namespaced_job(
                    name=job_name,
                    namespace=self.namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Foreground"),
                )
            except Exception:
                self.log.warning("Failed to delete Job %s", job_name, exc_info=True)

    def _build_k8s_job(self, job_name, grader_path, code, seed, grader_config=None):
        """Return a kubernetes Job manifest for the given grading run."""
        from kubernetes import client as k8s_client

        if grader_config is None:
            grader_config = {}

        # The entrypoint takes: GRADER_FILE SEED
        # The grader scripts are baked into the course-specific image at grader_path.
        # working_dir must stay at /grader (the WORKDIR of the base image) so that
        # `python -m grader_support.entrypoint` can locate the grader_support package.
        grader_abs = str(grader_path)

        return k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                labels={
                    "app.kubernetes.io/component": "xqueue-grader",
                    "app.kubernetes.io/managed-by": "xqueue-watcher",
                },
            ),
            spec=k8s_client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=self.timeout,
                ttl_seconds_after_finished=300,
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(
                        labels={
                            "app.kubernetes.io/component": "xqueue-grader",
                            "app.kubernetes.io/managed-by": "xqueue-watcher",
                        }
                    ),
                    spec=k8s_client.V1PodSpec(
                        restart_policy="Never",
                        automount_service_account_token=False,
                        security_context=k8s_client.V1PodSecurityContext(
                            run_as_non_root=True,
                            run_as_user=1000,
                            seccomp_profile=k8s_client.V1SeccompProfile(
                                type="RuntimeDefault",
                            ),
                        ),
                        # Grader scripts are baked into the course-specific image
                        # (no volume mount required).  The image extends
                        # grader_support/Dockerfile.base and includes the grader
                        # files at the path referenced by grader_abs.
                        containers=[
                            k8s_client.V1Container(
                                name="grader",
                                image=self._effective_image(),
                                image_pull_policy=self.image_pull_policy,
                                # entrypoint signature: GRADER_FILE SEED
                                args=[grader_abs, str(seed)],
                                working_dir="/grader",
                                env=[
                                    k8s_client.V1EnvVar(
                                        name="SUBMISSION_CODE",
                                        value=code,
                                    ),
                                    k8s_client.V1EnvVar(
                                        name="GRADER_LANGUAGE",
                                        value=grader_config.get("lang", "en"),
                                    ),
                                    k8s_client.V1EnvVar(
                                        name="HIDE_OUTPUT",
                                        value="1" if grader_config.get("hide_output") else "0",
                                    ),
                                ],
                                resources=k8s_client.V1ResourceRequirements(
                                    limits={
                                        "cpu": self.cpu_limit,
                                        "memory": self.memory_limit,
                                    },
                                    requests={
                                        "cpu": "100m",
                                        "memory": "64Mi",
                                    },
                                ),
                                security_context=k8s_client.V1SecurityContext(
                                    allow_privilege_escalation=False,
                                    read_only_root_filesystem=True,
                                    capabilities=k8s_client.V1Capabilities(drop=["ALL"]),
                                    seccomp_profile=k8s_client.V1SeccompProfile(
                                        type="RuntimeDefault",
                                    ),
                                ),
                                volume_mounts=[
                                    k8s_client.V1VolumeMount(
                                        name="tmp",
                                        mount_path="/tmp",
                                    ),
                                ],
                            )
                        ],
                        volumes=[
                            # emptyDir at /tmp is required because read_only_root_filesystem=True
                            # prevents writes to the root FS; the entrypoint writes the student
                            # submission to /tmp/submission.py before executing it.
                            k8s_client.V1Volume(
                                name="tmp",
                                empty_dir=k8s_client.V1EmptyDirVolumeSource(
                                    size_limit="50Mi",
                                ),
                            ),
                        ],
                    )
                ),
            ),
        )

    def _wait_and_collect_k8s(self, batch_v1, core_v1, job_name, timeout):
        """Poll until the Job completes, then return its pod's stdout bytes."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = batch_v1.read_namespaced_job(name=job_name, namespace=self.namespace)
            if job.status.succeeded:
                break
            if job.status.failed:
                raise RuntimeError(f"Grading Job {job_name} failed.")
            time.sleep(1)
        else:
            raise RuntimeError(
                f"Grading Job {job_name} exceeded timeout of {timeout}s."
            )

        pods = core_v1.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"job-name={job_name}",
        )
        if not pods.items:
            raise RuntimeError(f"No pods found for Job {job_name}.")

        pod_name = pods.items[0].metadata.name
        # The Kubernetes Python client deserializes the log response body via
        # json.loads() then casts to str(), turning valid JSON into Python repr
        # (single-quoted dict).  Pass _preload_content=False to get the raw
        # urllib3 response object and read the bytes directly, bypassing the
        # client's deserialisation entirely.
        raw = core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=self.namespace,
            container="grader",
            _preload_content=False,
        )
        log = raw.data.decode("utf-8")
        # Scan backwards to find the last non-empty line (the JSON result).
        # Earlier lines may be stderr interleaved by the Kubernetes log API.
        json_line = None
        for line in reversed(log.splitlines()):
            stripped = line.strip()
            if stripped:
                json_line = stripped
                break
        if not json_line:
            raise RuntimeError(f"No output from grading pod {pod_name}.")
        return json_line.encode("utf-8")

    def _run_docker(self, grader_path, code, seed, grader_config=None):
        """Run a local Docker container and return stdout bytes."""
        try:
            import docker as docker_sdk
        except ImportError:
            raise RuntimeError(
                "The 'docker' package is required for the docker backend. "
                "Install it with: uv add docker"
            )

        if grader_config is None:
            grader_config = {}

        grader_dir = str(Path(grader_path).parent.resolve())
        grader_rel = str(Path(grader_path).name)
        # Mount the problem directory at /graders/ (not /grader/ which would
        # overwrite the base image's grader_support package).  Pass the grader
        # as an absolute in-container path.
        container_grader_path = f"/graders/{grader_rel}"

        # When xqueue-watcher runs inside a container, grader_dir is a
        # container-internal path.  docker_host_grader_root maps grader_root to
        # the equivalent directory on the Docker host so that bind-mounts reach
        # the correct location.
        if self.docker_host_grader_root:
            rel = Path(grader_path).parent.resolve().relative_to(
                Path(self.grader_root).resolve()
            )
            host_grader_dir = str(Path(self.docker_host_grader_root) / rel)
        else:
            host_grader_dir = grader_dir

        env = {
            "SUBMISSION_CODE": code,
            "GRADER_LANGUAGE": grader_config.get("lang", "en"),
            "HIDE_OUTPUT": "1" if grader_config.get("hide_output") else "0",
        }

        client = docker_sdk.from_env()
        try:
            # Run detached so we can enforce a wall-clock timeout via container.wait().
            # containers.run() does not accept a timeout argument; using detach=True
            # lets us call container.wait(timeout=...) to cap execution time.
            container = client.containers.run(
                image=self._effective_image(),
                # entrypoint signature: GRADER_FILE SEED
                command=[container_grader_path, str(seed)],
                working_dir="/grader",
                environment=env,
                volumes={host_grader_dir: {"bind": "/graders", "mode": "ro"}},
                mem_limit=_parse_memory_bytes(self.memory_limit),
                nano_cpus=int(_parse_cpu_millis(self.cpu_limit) * 1_000_000),
                network_disabled=True,
                read_only=True,
                detach=True,
                stdout=True,
                stderr=False,
            )
            try:
                exit_info = container.wait(timeout=self.timeout)
                if exit_info.get("StatusCode", 0) != 0:
                    stderr = container.logs(stdout=False, stderr=True)
                    raise RuntimeError(
                        f"Grading container exited with non-zero status: {exit_info}. "
                        f"stderr: {stderr[:2000] if stderr else ''}"
                    )
                result = container.logs(stdout=True, stderr=False)
            except Exception as exc:
                # Catch ReadTimeout (requests.exceptions.ReadTimeout) from container.wait()
                # and any other unexpected error, converting to a clear RuntimeError.
                exc_name = type(exc).__name__
                if "Timeout" in exc_name or "timeout" in str(exc).lower():
                    raise RuntimeError(
                        f"Grading container timed out after {self.timeout}s."
                    ) from exc
                raise
            finally:
                container.remove(force=True)
        except docker_sdk.errors.ContainerError as exc:
            raise RuntimeError(
                f"Grading container exited with error: {exc}"
            ) from exc

        return result if isinstance(result, bytes) else result.encode("utf-8")

    # ------------------------------------------------------------------
    # Public grading interface
    # ------------------------------------------------------------------

    def grade(self, grader_path, grader_config, submission):
        """
        Grade a student submission by running the full pipeline inside a container.

        The container (grader_support.entrypoint) handles all grading steps:
          - Loading the grader module (baked into the image)
          - Validating the submission format
          - Preprocessing and running the staff answer and student submission
          - Comparing results test-by-test
          - Returning the final grade as JSON

        Returns a dict with keys: correct, score, errors, tests.
        """
        if not isinstance(submission, str):
            self.log.warning("Submission is NOT unicode")

        results = {
            "errors": [],
            "tests": [],
            "correct": False,
            "score": 0,
        }

        if grader_config.get("skip_grader", False):
            results["correct"] = True
            results["score"] = 1
            self.log.debug("Skipping the grader.")
            return results

        seed = str(random.randint(0, 20000))

        try:
            output = self._run(grader_path, submission, seed, grader_config)
            self.log.debug(
                "Raw container output (%d bytes) for grader %s: %r",
                len(output),
                grader_path,
                output[:4096],
            )
            grade_result = json.loads(output.decode("utf-8"))
            return grade_result
        except json.JSONDecodeError:
            self.log.error(
                "Failed to parse container output as JSON for grader %s. "
                "Raw output (%d bytes): %r",
                grader_path,
                len(output),
                output[:4096],
            )
            raise
        except Exception:
            self.log.exception(
                "Grading container failed. grader = %s", grader_path
            )
            results["errors"].append(
                "There was a problem running your code (Staff debug). "
                "Please contact the course staff for assistance."
            )
            return results



def _parse_cpu_millis(cpu_str):
    """Convert a Kubernetes CPU string like '500m' or '1' to a float of millicores."""
    cpu_str = str(cpu_str).strip()
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])
    return float(cpu_str) * 1000


def _parse_memory_bytes(memory_str):
    """Convert a Kubernetes/Docker memory string to bytes for the Docker API.

    Handles IEC binary suffixes (Ki, Mi, Gi, Ti) and SI decimal suffixes
    (K, M, G, T).  Plain integers are returned unchanged.

    Examples:
        "256Mi" -> 268435456
        "1Gi"   -> 1073741824
        "512M"  -> 512000000
        "1024"  -> 1024
    """
    s = str(memory_str).strip()
    iec = {"Ti": 1024**4, "Gi": 1024**3, "Mi": 1024**2, "Ki": 1024}
    si  = {"T": 1000**4,  "G": 1000**3,  "M": 1000**2,  "K": 1000}
    for suffix, factor in iec.items():
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * factor)
    for suffix, factor in si.items():
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * factor)
    return int(s)
