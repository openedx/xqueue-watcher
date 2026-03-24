## Grader Base Image

This Dockerfile builds the base image that all course-specific grader images extend.

### What it contains

- Python 3.11 (slim)
- The `grader_support` package (test framework and runner used by all graders) at `/grader/grader_support/`
- A non-root `grader` user (UID 1000)
- An entrypoint that reads student submissions from the `SUBMISSION_CODE` environment variable

### Building

```bash
docker build -t grader-base:latest -f grader_support/Dockerfile.base .
```

Or via the Makefile:

```bash
make docker-build
```

### Course team usage

Course teams create their own image `FROM grader-base` and add their grader scripts plus any Python dependencies required by the graders.

#### Directory layout inside the container

```
/grader/                        ← WORKDIR (base image); grader_support lives here
└── grader_support/             ← test framework (gradelib, run, entrypoint)

/graders/                       ← course grader scripts (course team copies these)
└── ps01/
│   └── Problem1/
│       ├── grade_Problem1.py   ← grader script (defines `grader = Grader()`)
│       └── answer.py           ← reference solution
└── ml/
    └── cluster/
        └── grade_cluster.py
```

`grader_root` in the handler config should point to `/graders/` (or a subdirectory of it).  The `SUBMISSION_CODE` env var carries student code; the entrypoint writes it to `/tmp` (a writable tmpfs even when the root filesystem is read-only).

#### Example course Dockerfile

```dockerfile
# syntax=docker/dockerfile:1
ARG GRADER_BASE_IMAGE=ghcr.io/mitodl/xqueue-watcher-grader-base:latest
FROM ${GRADER_BASE_IMAGE}

# pip must run as root; the base image ends with USER grader.
USER root
RUN pip install --no-cache-dir numpy==1.26.4 scipy==1.13.0

# Copy grader scripts to /graders/.  Do NOT copy them to /grader/ — that
# would overwrite the grader_support package from the base image.
COPY --chown=grader:grader graders/ /graders/

USER grader
```

#### Example handler config (`conf.d/my-course.json`)

```json
{
  "my-course-queue": {
    "SERVER": "http://xqueue:18040",
    "CONNECTIONS": 2,
    "AUTH": ["lms", "lms"],
    "HANDLERS": [
      {
        "HANDLER": "xqueue_watcher.containergrader.ContainerGrader",
        "KWARGS": {
          "grader_root": "/graders/",
          "image": "registry.example.com/my-course-grader:latest",
          "backend": "kubernetes",
          "cpu_limit": "500m",
          "memory_limit": "256Mi",
          "timeout": 20
        }
      }
    ]
  }
}
```

The `grader` field inside each xqueue submission payload should be a path **relative to `grader_root`**, e.g. `"ps01/Problem1/grade_Problem1.py"`.

### Security properties

Grader containers run with:
- Non-root user (UID 1000)
- Read-only root filesystem (`/tmp` is a tmpfs for submission files)
- No network access (`network_disabled: true` / Kubernetes NetworkPolicy)
- CPU and memory limits enforced by the container runtime
- Hard wall-clock timeout via `activeDeadlineSeconds` (Kubernetes) or `timeout` (Docker)

### Important: `gradelib` compatibility

The `grader_support/__init__.py` injects the framework's Python 3 `gradelib` and
`graderutil` modules into `sys.modules` before any grader file is imported.  This
means grader scripts that do `from gradelib import *` receive the framework version
automatically, even if a legacy `gradelib.py` exists elsewhere on disk.  Course teams
do not need to ship their own copy of `gradelib.py`.
