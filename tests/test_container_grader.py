"""
Unit tests for ContainerGrader.

Uses mock objects for the Docker SDK and kubernetes client to test container
execution paths without requiring a live Docker daemon or cluster.
"""

import json
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import pytest

from xqueue_watcher.containergrader import ContainerGrader, _parse_cpu_millis, _parse_memory_bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_grader(backend="docker", **kwargs):
    defaults = dict(grader_root="/graders", image="course-grader:v1", backend=backend)
    defaults.update(kwargs)
    return ContainerGrader(**defaults)


# ---------------------------------------------------------------------------
# _parse_cpu_millis
# ---------------------------------------------------------------------------

class TestParseCpuMillis:
    def test_millicores(self):
        assert _parse_cpu_millis("500m") == 500.0

    def test_whole_cores(self):
        assert _parse_cpu_millis("2") == 2000.0

    def test_fractional_cores(self):
        assert _parse_cpu_millis("0.5") == 500.0


# ---------------------------------------------------------------------------
# ContainerGrader.__init__
# ---------------------------------------------------------------------------

class TestContainerGraderInit:
    def test_valid_kubernetes_backend(self):
        g = make_grader(backend="kubernetes")
        assert g.backend == "kubernetes"

    def test_valid_docker_backend(self):
        g = make_grader(backend="docker")
        assert g.backend == "docker"

    def test_invalid_backend(self):
        with pytest.raises(ValueError, match="Unsupported backend"):
            make_grader(backend="podman")

    def test_defaults_from_env_when_no_kwargs(self):
        """With no kwargs, values come from env defaults (all at baseline)."""
        g = ContainerGrader(grader_root="/graders", image="img:latest")
        assert g.backend == "kubernetes"
        assert g.namespace == "default"
        assert g.cpu_limit == "500m"
        assert g.memory_limit == "256Mi"
        assert g.timeout == 20

    def test_kwargs_override_env_defaults(self):
        """Explicit kwargs always win over env defaults."""
        env = {
            "XQWATCHER_GRADER_BACKEND": "docker",
            "XQWATCHER_GRADER_NAMESPACE": "env-ns",
            "XQWATCHER_GRADER_CPU_LIMIT": "250m",
            "XQWATCHER_GRADER_MEMORY_LIMIT": "128Mi",
            "XQWATCHER_GRADER_TIMEOUT": "5",
        }
        with patch.dict("os.environ", env):
            g = ContainerGrader(
                grader_root="/graders",
                image="img:latest",
                backend="kubernetes",
                namespace="kwarg-ns",
                cpu_limit="1000m",
                memory_limit="512Mi",
                timeout=99,
            )
        assert g.backend == "kubernetes"
        assert g.namespace == "kwarg-ns"
        assert g.cpu_limit == "1000m"
        assert g.memory_limit == "512Mi"
        assert g.timeout == 99

    def test_env_defaults_applied_when_no_kwargs(self):
        """Env vars are used when the corresponding kwarg is absent."""
        env = {
            "XQWATCHER_GRADER_BACKEND": "docker",
            "XQWATCHER_GRADER_NAMESPACE": "grading",
            "XQWATCHER_GRADER_CPU_LIMIT": "750m",
            "XQWATCHER_GRADER_MEMORY_LIMIT": "512Mi",
            "XQWATCHER_GRADER_TIMEOUT": "30",
        }
        with patch.dict("os.environ", env):
            g = ContainerGrader(grader_root="/graders", image="img:latest")
        assert g.backend == "docker"
        assert g.namespace == "grading"
        assert g.cpu_limit == "750m"
        assert g.memory_limit == "512Mi"
        assert g.timeout == 30

    def test_invalid_backend_from_env_raises(self):
        with patch.dict("os.environ", {"XQWATCHER_GRADER_BACKEND": "podman"}):
            with pytest.raises(ValueError, match="Unsupported backend"):
                ContainerGrader(grader_root="/graders", image="img:latest")


# ---------------------------------------------------------------------------
# _build_k8s_job
# ---------------------------------------------------------------------------

class TestBuildK8sJob:
    def setup_method(self):
        self.grader = make_grader(
            backend="kubernetes",
            namespace="test-ns",
            cpu_limit="1000m",
            memory_limit="512Mi",
            timeout=30,
        )

    def _build(self, job_name="test-job", grader_path="/graders/grade.py", code="code", seed=42):
        return self.grader._build_k8s_job(job_name, grader_path, code, seed)

    def test_job_name(self):
        job = self._build(job_name="xqueue-grader-abc123")
        assert job.metadata.name == "xqueue-grader-abc123"

    def test_image(self):
        job = self._build()
        assert job.spec.template.spec.containers[0].image == "course-grader:v1"

    def test_args_are_grader_and_seed(self):
        # entrypoint takes GRADER_FILE SEED (no submission.py positional arg)
        job = self._build(grader_path="/graders/ps07/grade.py", seed=99)
        assert job.spec.template.spec.containers[0].args == ["/graders/ps07/grade.py", "99"]

    def test_submission_code_env(self):
        job = self._build(code="x = 1")
        env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
        assert env["SUBMISSION_CODE"] == "x = 1"

    def test_grader_language_env_default(self):
        job = self._build()
        env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
        assert env["GRADER_LANGUAGE"] == "en"

    def test_grader_language_from_config(self):
        job = self.grader._build_k8s_job("job", "/g/grade.py", "code", 1, {"lang": "fr"})
        env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
        assert env["GRADER_LANGUAGE"] == "fr"

    def test_hide_output_env_default_off(self):
        job = self._build()
        env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
        assert env["HIDE_OUTPUT"] == "0"

    def test_hide_output_env_when_set(self):
        job = self.grader._build_k8s_job("job", "/g/grade.py", "code", 1, {"hide_output": True})
        env = {e.name: e.value for e in job.spec.template.spec.containers[0].env}
        assert env["HIDE_OUTPUT"] == "1"

    def test_resource_limits(self):
        job = self._build()
        limits = job.spec.template.spec.containers[0].resources.limits
        assert limits["cpu"] == "1000m"
        assert limits["memory"] == "512Mi"

    def test_tmp_empty_dir_volume_present(self):
        job = self._build()
        volumes = job.spec.template.spec.volumes
        tmp_vol = next((v for v in volumes if v.name == "tmp"), None)
        assert tmp_vol is not None, "emptyDir volume at /tmp is required"
        assert tmp_vol.empty_dir is not None

    def test_tmp_volume_mounted_at_tmp(self):
        job = self._build()
        mounts = job.spec.template.spec.containers[0].volume_mounts
        tmp_mount = next((m for m in mounts if m.name == "tmp"), None)
        assert tmp_mount is not None
        assert tmp_mount.mount_path == "/tmp"

    def test_read_only_root_filesystem(self):
        job = self._build()
        sc = job.spec.template.spec.containers[0].security_context
        assert sc.read_only_root_filesystem is True

    def test_no_privilege_escalation(self):
        job = self._build()
        sc = job.spec.template.spec.containers[0].security_context
        assert sc.allow_privilege_escalation is False

    def test_backoff_limit_zero(self):
        job = self._build()
        assert job.spec.backoff_limit == 0

    def test_active_deadline_matches_timeout(self):
        job = self._build()
        assert job.spec.active_deadline_seconds == 30


# ---------------------------------------------------------------------------
# _run_docker
# ---------------------------------------------------------------------------

def _make_mock_client(exit_code=0, stdout_data=b'{"correct": true}', stderr_data=b""):
    """Return a (client, container) pair pre-configured with given outputs."""
    container = mock.MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}

    def logs_side_effect(stdout=True, stderr=False):
        if stderr and not stdout:
            return stderr_data
        return stdout_data

    container.logs.side_effect = logs_side_effect
    client = mock.MagicMock()
    client.containers.run.return_value = container
    return client, container


class TestRunDocker:
    def setup_method(self):
        self.grader = make_grader(backend="docker", timeout=10)

    def _run(self, client, code="print('hi')", seed=42, grader_config=None):
        with mock.patch("docker.from_env", return_value=client):
            return self.grader._run_docker(
                "/graders/ps07/grade.py", code, seed, grader_config or {}
            )

    def test_success_returns_stdout(self):
        client, _ = _make_mock_client(stdout_data=b'{"correct": true}')
        result = self._run(client)
        assert result == b'{"correct": true}'

    def test_container_removed_on_success(self):
        client, container = _make_mock_client()
        self._run(client)
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_on_failure(self):
        client, container = _make_mock_client(exit_code=1, stderr_data=b"Traceback...")
        with pytest.raises(RuntimeError):
            self._run(client)
        container.remove.assert_called_once_with(force=True)

    def test_non_zero_exit_raises(self):
        client, _ = _make_mock_client(exit_code=1, stderr_data=b"Error!")
        with pytest.raises(RuntimeError, match="non-zero status"):
            self._run(client)

    def test_stderr_included_in_error_message(self):
        client, _ = _make_mock_client(exit_code=1, stderr_data=b"ImportError: missing module")
        with pytest.raises(RuntimeError, match="ImportError"):
            self._run(client)

    def test_timeout_raises_runtime_error(self):
        client, container = _make_mock_client()
        container.wait.side_effect = Exception("ReadTimeout")
        with pytest.raises(RuntimeError, match="timed out"):
            self._run(client)

    def test_missing_docker_sdk_raises(self):
        with mock.patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(RuntimeError, match="'docker' package"):
                self.grader._run_docker("/graders/grade.py", "code", 1, {})

    def test_string_result_converted_to_bytes(self):
        client, container = _make_mock_client()
        container.logs.side_effect = None
        container.logs.return_value = '{"correct": false}'
        result = self._run(client)
        assert isinstance(result, bytes)

    def test_entrypoint_args_are_grader_and_seed(self):
        """Container command should be [grader_path, seed] — not 3 args."""
        client, _ = _make_mock_client()
        self._run(client, seed=99)
        call_kwargs = client.containers.run.call_args
        command = call_kwargs.kwargs.get("command") or call_kwargs[1].get("command")
        assert len(command) == 2
        assert command[1] == "99"

    def test_grader_language_passed_as_env(self):
        client, _ = _make_mock_client()
        self._run(client, grader_config={"lang": "es"})
        call_kwargs = client.containers.run.call_args
        env = call_kwargs.kwargs.get("environment") or call_kwargs[1].get("environment")
        assert env.get("GRADER_LANGUAGE") == "es"


# ---------------------------------------------------------------------------
# grade() public interface
# ---------------------------------------------------------------------------

class TestGrade:
    def setup_method(self):
        self.grader = make_grader()

    def _grade(self, submission="x = 1", grader_config=None):
        if grader_config is None:
            grader_config = {}
        return self.grader.grade(
            grader_path=Path("/graders/ps07/grade.py"),
            grader_config=grader_config,
            submission=submission,
        )

    def test_skip_grader_returns_correct(self):
        result = self._grade(grader_config={"skip_grader": True})
        assert result["correct"] is True
        assert result["score"] == 1

    def test_container_result_returned_directly(self):
        grade_json = {"correct": True, "score": 1.0, "errors": [], "tests": []}
        with mock.patch.object(self.grader, "_run", return_value=json.dumps(grade_json).encode()):
            result = self._grade()
        assert result["correct"] is True
        assert result["score"] == 1.0

    def test_container_failure_returns_error_dict(self):
        with mock.patch.object(self.grader, "_run", side_effect=RuntimeError("container died")):
            result = self._grade()
        assert result["correct"] is False
        assert result["errors"]

    def test_large_submission_logs_warning(self, caplog):
        import logging
        large_code = "x = 1\n" * 10_000  # ~70 KB
        grade_json = {"correct": False, "score": 0.0, "errors": [], "tests": []}
        # Mock the backend method so _run() still executes the size check.
        with mock.patch.object(
            self.grader, "_run_docker", return_value=json.dumps(grade_json).encode()
        ):
            with caplog.at_level(logging.WARNING):
                self._grade(submission=large_code)
        assert any("large" in r.message.lower() for r in caplog.records)
