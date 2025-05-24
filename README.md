# Ansible-Lint Microservices

## Fast API
<img width="1509" alt="Screenshot 2025-05-23 at 10 32 39 PM" src="https://github.com/user-attachments/assets/385ca219-4e0a-48f9-9723-889e2331263e" />

## MCP Inspector UI 
<img width="1509" alt="Screenshot 2025-05-23 at 10 31 44 PM" src="https://github.com/user-attachments/assets/1fa14504-3329-48d9-9c7e-78f3e56cc359" />


This repository contains two complementary services for running **Ansible Lint** at scale:

1. **ansible-lint-api**
   A FastAPI-based HTTP microservice that lints Ansible playbooks and returns structured JSON results and Prometheus metrics.

2. **ansible-lint-mcp**
   An MCP-compatible server wrapping the same lint functionality as a JSON/SSE “tool” for downstream agents and pipelines.

Both images are based on Red Hat UBI 8 Python and are OpenShift-friendly (non-root, group-writable directories).

---

## 🚀 Quickstart

### Prerequisites

* Podman (or Docker) v4+
* OpenShift CLI (`oc`) v4+
* Access to Quay.io (or your preferred registry)

---

## 🏗 Building & Pushing Images

Build for AMD64 and tag:

```bash
# In the ansible-lint-api directory
podman build \
  --platform=linux/amd64 \
  -f Dockerfile.ansible-lint-api \
  -t quay.io/rbrhssa/ansible-lint-api:latest .

# In the ansible-lint-mcp directory
podman build \
  --platform=linux/amd64 \
  -f Dockerfile.ansible-lint-mcp \
  -t quay.io/rbrhssa/ansible-lint-mcp:latest .
```

Push to Quay.io:

```bash
podman push quay.io/rbrhssa/ansible-lint-api:latest
podman push quay.io/rbrhssa/ansible-lint-mcp:latest
```

---

## 🐍 Using Python Virtual Environments

You can run each service locally without containers by using a `venv`.

### ansible-lint-api

```bash
# Create and activate venv
python3 -m venv .venv-api
source .venv-api/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Run with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8080
# or with Python module
python -m uvicorn main:app --host 0.0.0.0 --port 8080
```

### ansible-lint-mcp

```bash
# Create and activate venv
python3 -m venv .venv-mcp
source .venv-mcp/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r mcp-requirements.txt

# Run directly
python server.py
# or via uvicorn if server.py exposes FastAPI app
uvicorn server:app --host 0.0.0.0 --port 8090
```

Deactivate when done:

```bash
deactivate
```

---

## 🏃 Running in Containers Locally

```bash
# Start the lint API (default port 8080)
podman run --rm -p 8080:8080 quay.io/rbrhssa/ansible-lint-api:latest

# Start the MCP server (default port 8090)
podman run --rm -p 8090:8090 quay.io/rbrhssa/ansible-lint-mcp:latest
```

**ansible-lint-api**

* `GET  /health` → `{ "status": "ok" }`
* `GET  /metrics` → Prometheus metrics
* `POST /v1/lint/{profile}`

  * Body: `file` upload (YAML playbook)
  * Profiles: `basic`, `production`
  * Response: JSON summary of lint issues

**ansible-lint-mcp**

* `POST /v1/tools` → List available tools
* `POST /v1/tools?tool=lint_ansible_playbook` → Run lint, get JSON output
* `GET  /sse` → Server-Sent Events stream of lint progress

---

## 📦 OpenShift Deployment

These images run under the Restricted SCC without extra privileges:

1. **Create an OpenShift app**

   ```bash
   oc new-app quay.io/rbrhssa/ansible-lint-api:latest \
     --name=ansible-lint-api
   oc new-app quay.io/rbrhssa/ansible-lint-mcp:latest \
     --name=ansible-lint-mcp
   ```

2. **Expose routes**

   ```bash
   oc expose svc/ansible-lint-api --port=8080
   oc expose svc/ansible-lint-mcp --port=8090
   ```

3. **Verify**

   ```bash
   oc get pods
   oc logs deployment/ansible-lint-api
   curl https://ansible-lint-api-yourcluster.apps.example.com/health
   ```

---

## 🛠 Configuration

Both services respect these environment variables:

| Variable       | Default       | Description                             |
| -------------- | ------------- | --------------------------------------- |
| `PORT`         | `8080`/`8090` | HTTP listen port for each service       |
| `LOG_LEVEL`    | `INFO`        | Python logging level                    |
| `CORS_ORIGINS` | `*`           | Comma-separated list of allowed origins |

---

## 📖 API Reference

See the built-in OpenAPI docs:

* **ansible-lint-api** → `http://<host>:8080/docs`
* **ansible-lint-mcp** → `http://<host>:8090/docs`

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Add tests / update documentation
4. Submit a PR and we’ll review!

---

## 📝 License

This project is licensed under the Apache 2.0 License. See [LICENSE](./LICENSE) for details.
