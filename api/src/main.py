#!/usr/bin/env python3
import os
import subprocess
import tempfile
import logging
import shutil
import time
import contextvars
from uuid import uuid4
from typing import List, Literal

from fastapi import FastAPI, HTTPException, UploadFile, File, status, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST


# ------------------------------------------------------------------------------
# Globals
SHOW_PROFILE_SUPPORTED = False

# ------------------------------------------------------------------------------
# Settings
class Settings(BaseSettings):
    ansible_lint_cmd: str = "ansible-lint"
    lint_timeout_seconds: int = 60
    max_upload_size_bytes: int = 5 * 1024 * 1024
    allowed_origins: List[str] = ["*"]
    log_level: str = "INFO"

    class Config:
        env_file = ".env"

settings = Settings()
SUPPORTED_PROFILES = ["basic", "production", "safety", "test", "minimal"]

# ------------------------------------------------------------------------------
# Logging Setup
REQUEST_ID_CTX = contextvars.ContextVar("request_id", default="-")

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = REQUEST_ID_CTX.get()
        return True

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","request_id":"%(request_id)s","message":"%(message)s"}'
)
logger = logging.getLogger("ansible-lint-service")
logger.addFilter(RequestIdFilter())

# ------------------------------------------------------------------------------
# Prometheus Metrics
REQUEST_COUNT = Counter("ansible_lint_requests_total", "Total ansible-lint requests", ["profile", "exit_code"])
REQUEST_LATENCY = Histogram("ansible_lint_request_latency_seconds", "Latency of ansible-lint requests", ["profile"])
TIMEOUT_COUNT = Counter("ansible_lint_timeouts_total", "Number of ansible-lint timeouts")
ERROR_COUNT = Counter("ansible_lint_errors_total", "Number of internal errors in lint runner")

# ------------------------------------------------------------------------------
# FastAPI Setup
app = FastAPI(title="Ansible Lint API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = str(uuid4())
    REQUEST_ID_CTX.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response

# ------------------------------------------------------------------------------
# Feature Detection
def detect_ansible_lint_features():
    global SHOW_PROFILE_SUPPORTED
    try:
        output = subprocess.check_output([settings.ansible_lint_cmd, "--help"], text=True)
        SHOW_PROFILE_SUPPORTED = "--show-profile" in output
        logger.info(f"SHOW_PROFILE_SUPPORTED = {SHOW_PROFILE_SUPPORTED}")
    except Exception as e:
        logger.warning(f"ansible-lint --help failed: {e}")

@app.on_event("startup")
def on_startup():
    detect_ansible_lint_features()

# ------------------------------------------------------------------------------
# Models
class LintResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str

class ProfilesResponse(BaseModel):
    profiles: List[str]

class HealthResponse(BaseModel):
    status: str

# ------------------------------------------------------------------------------
# Core Lint Logic
async def run_ansible_lint(playbook: str, profile: str) -> LintResult:
    def _invoke() -> LintResult:
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "playbook.yml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(playbook)

        cmd = [settings.ansible_lint_cmd, f"--profile={profile}", "--nocolor"]
        if SHOW_PROFILE_SUPPORTED:
            cmd.append("--show-profile")
        cmd.append(path)

        logger.info(f"Running command: {' '.join(cmd)}")
        start = time.time()
        exit_code, stdout, stderr = 1, "", ""

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.lint_timeout_seconds)
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            TIMEOUT_COUNT.inc()
            stderr = f"ansible-lint timed out after {settings.lint_timeout_seconds}s"
        except Exception as e:
            ERROR_COUNT.inc()
            stderr = f"ansible-lint failed: {e}"
        finally:
            REQUEST_LATENCY.labels(profile=profile).observe(time.time() - start)
            REQUEST_COUNT.labels(profile=profile, exit_code=str(exit_code)).inc()
            shutil.rmtree(tmpdir, ignore_errors=True)

        return LintResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

    return await run_in_threadpool(_invoke)

# ------------------------------------------------------------------------------
# Endpoints
@app.post("/v1/lint/{profile}", response_model=LintResult)
async def lint_playbook(profile: Literal["basic", "production", "safety", "test", "minimal"], file: UploadFile = File(...)):
    if not file.filename.lower().endswith((".yml", ".yaml")):
        raise HTTPException(status_code=400, detail="Only .yml/.yaml files are accepted")
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    return await run_ansible_lint(content.decode("utf-8"), profile)

@app.get("/v1/lint/test", response_model=LintResult)
async def test_lint_playbook(profile: Literal["basic", "production", "safety", "test", "minimal"] = "basic"):
    path = os.getenv("CI_TEST_PLAYBOOK_PATH", "tests/hello.yml")
    
    # Check if the test playbook file exists
    if not os.path.exists(path):
        logger.error(f"Test playbook not found at path: {path}")
        raise HTTPException(status_code=404, detail=f"Test playbook not found at {path}")
    
    # Validate profile parameter
    if profile not in SUPPORTED_PROFILES:
        raise HTTPException(status_code=400, detail=f"Invalid profile '{profile}'. Supported profiles: {SUPPORTED_PROFILES}")
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(f"Running test lint with profile: {profile}")
        return await run_ansible_lint(content, profile)
    except Exception as e:
        logger.error(f"Error reading test playbook: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading test playbook: {str(e)}")

@app.get("/v1/profiles", response_model=ProfilesResponse)
def list_profiles():
    return ProfilesResponse(profiles=SUPPORTED_PROFILES)

@app.get("/v1/health", response_model=HealthResponse)
def health():
    return HealthResponse(status="ok")

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# ------------------------------------------------------------------------------
# Entrypoint
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))