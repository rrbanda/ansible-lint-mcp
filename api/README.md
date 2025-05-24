# Ansible Lint API

A production-ready FastAPI service to validate Ansible playbooks using `ansible-lint`, with support for multiple profiles like `basic`, `production`, and `safe`.

## 🏁 Getting Started

### 1. Install dependencies

```bash
pip install -r requirements.txt
````

### 2. Run the API server

```bash
uvicorn main:app --host 0.0.0.0 --port 8080
```

---

## 🧪 Endpoints

### ✅ Health Check

```bash
curl http://localhost:8080/health
```

### 📜 List Supported Profiles

```bash
curl http://localhost:8080/profiles
```

### 🔍 Lint a Playbook

```bash
curl -X POST http://localhost:8080/lint/production \
  -F "file=@your_playbook.yml"
```

Replace `production` with one of:

* `basic`
* `production`
* `safe`
* `test`
* `minimal`

---

## 📦 Output Format

```json
{
  "exit_code": 2,
  "stdout": "... ansible-lint output ...",
  "stderr": "... warnings/errors ..."
}
```

---

## 🚀 Deploying to Production

Use Gunicorn with multiple workers:

```bash
gunicorn main:app -k uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:8080
```

---

## 🔐 Security Notes

* This service executes shell commands — deploy in a secure, isolated environment.
* Consider file size and rate limiting in production.



