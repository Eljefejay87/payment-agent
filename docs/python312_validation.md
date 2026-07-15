# Python 3.12 Validation

The Railway container runtime is pinned to `python:3.12-slim` in the project
Dockerfile. Validate every release in an isolated Python 3.12 environment
before deployment; do not use production credentials or start a mailbox scan.

## Required environment

- Python 3.12.x and `venv` support;
- a clean virtual environment created with that interpreter;
- the pinned dependencies from `requirements.txt`;
- the test-only dependencies from `requirements-test.txt`;
- writable temporary storage for unit tests.

Create the environment and run the complete suite:

```bash
python3.12 -m venv .venv-py312
.venv-py312/bin/python -m pip install --upgrade pip
.venv-py312/bin/python -m pip install -r requirements.txt
.venv-py312/bin/python -m pip install -r requirements-test.txt
.venv-py312/bin/python -m unittest discover -s tests
```

For a container-equivalent check, build the dedicated `test` stage and run the
suite in an ephemeral container with no `.env`, Railway variables, or
persistent production volume attached:

```bash
docker build --target test -t payment-agent-test:local .
docker run --rm --network none payment-agent-test:local \
  python -m unittest discover -s tests
```

The default final Docker stage is `runtime`; it installs only
`requirements.txt`. `requirements-test.txt` is not part of the production
image.

## Compatibility scope

The runtime uses standard-library `pathlib`, `tempfile`, JSON, SQLite, and
timezone-aware datetime APIs. Its direct pinned dependencies (`msal`,
`python-dotenv`, `requests`, `google-auth`, `schedule`, `Pillow`, and `pypdf`)
each support Python 3.12. PDF extraction imports the declared `pypdf` package;
it does not rely on a local development-runtime path.

This validation must also preserve the existing degraded Graph behavior: mocked
authentication failures must leave the worker running, report Graph as
unavailable, and require attention without exposing token or mailbox data.
