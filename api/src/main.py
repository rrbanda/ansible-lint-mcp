#!/usr/bin/env python3
"""
Ansible Lint Service
--------------------

A production-ready microservice that lints uploaded Ansible playbooks
using ansible-lint, with:

• Config via Pydantic BaseSettings  
• Non-blocking subprocess calls  
• File-size and type validation  
• Literal path parameters for profiles  
• Structured JSON logging with per-request correlation IDs  
• Prometheus metrics (requests, latencies, timeouts, errors)  
• CORS locked to trusted origins  
• OpenAPI docs (tags, summaries, response models)  
• Liveness (/v1/health) and readiness (/v1/ready) checks  
"""

import os
import subprocess
import tempfile
import logging
import shutil
import time
import contextvars
from uuid import uuid4
from typing import List, Literal

from fastapi import (
    FastAPI, HTTPException, UploadFile, File,
    status, Request, Response
)
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

# Pydantic v2: BaseSettings lives in pydantic-settings package
try:
    from pydantic import BaseSettings
except ImportError:
    from pydantic_settings import BaseSettings

from pydantic import BaseModel

from prometheus_client import (
    Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
)

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
class Settings(BaseSettings):
    ansible_lint_cmd: str = "ansible-lint"
    lint_timeout_seconds: int = 60
    max_upload_size_bytes: int = 5 * 1024 * 1024   # 5 MB
    allowed_origins: List[str] = ["https://your-frontend.example.com"]
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()

SUPPORTED_PROFILES = ["basic", "production", "safe", "test", "minimal"]

# ------------------------------------------------------------------------------
# Logging + Correlation ID
# ------------------------------------------------------------------------------
REQUEST_ID_CTX = contextvars.ContextVar("request_id", default="-")

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = REQUEST_ID_CTX.get()
        return True

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='{"timestamp":"%(asctime)s",'
           '"level":"%(levelname)s",'
           '"logger":"%(name)s",'
           '"request_id":"%(request_id)s",'
           '"message":"%(message)s"}'
)
logger = logging.getLogger("ansible-lint-service")
logger.addFilter(RequestIdFilter())

# ------------------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "ansible_lint_requests_total",
    "Total ansible-lint requests",
    ["profile", "exit_code"]
)
REQUEST_LATENCY = Histogram(
    "ansible_lint_request_latency_seconds",
    "Latency of ansible-lint requests",
    ["profile"]
)
TIMEOUT_COUNT = Counter(
    "ansible_lint_timeouts_total",
    "Number of ansible-lint timeouts"
)
ERROR_COUNT = Counter(
    "ansible_lint_errors_total",
    "Number of internal errors in lint runner"
)

# ------------------------------------------------------------------------------
# FastAPI setup
# ------------------------------------------------------------------------------
app = FastAPI(
    title="Ansible Lint Service",
    version="1.0.0",
    openapi_tags=[
        {"name": "lint", "description": "Run ansible-lint on uploaded playbooks"},
        {"name": "meta", "description": "Health & metadata endpoints"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = str(uuid4())
    REQUEST_ID_CTX.set(rid)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response

# ------------------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------------------
class LintResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str

class ProfilesResponse(BaseModel):
    profiles: List[str]

class HealthResponse(BaseModel):
    status: str = "ok"

# ------------------------------------------------------------------------------
# Exception handlers
# ------------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    logger.warning(f"HTTP {exc.status_code}: {exc.detail}")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        {"detail": "Internal Server Error"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

# ------------------------------------------------------------------------------
# Readiness & Health
# ------------------------------------------------------------------------------
@app.get("/v1/ready", summary="Readiness probe", tags=["meta"])
def readiness():
    """Ensure ansible-lint binary is present."""
    if shutil.which(settings.ansible_lint_cmd) is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"'{settings.ansible_lint_cmd}' not found"
        )
    return {"status": "ready"}

@app.get("/v1/health", response_model=HealthResponse,
         summary="Liveness probe", tags=["meta"])
def health():
    return HealthResponse()

# ------------------------------------------------------------------------------
# Metrics endpoint
# ------------------------------------------------------------------------------
@app.get("/metrics", include_in_schema=False)
def metrics():
    data = generate_latest()
    return Response(data, media_type=CONTENT_TYPE_LATEST)

# ------------------------------------------------------------------------------
# Core lint runner
# ------------------------------------------------------------------------------
async def run_ansible_lint(playbook: str, profile: str) -> LintResult:
    def _invoke() -> LintResult:
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "playbook.yml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(playbook)

        cmd = [
            settings.ansible_lint_cmd,
            "--nocolor",
            "--profile", profile,
            path
        ]
        logger.info("Running subprocess", extra={"cmd": cmd})

        exit_code = 1
        stdout = ""
        stderr = ""
        start = time.time()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.lint_timeout_seconds
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired:
            TIMEOUT_COUNT.inc()
            stderr = (f"ansible-lint timed out "
                      f"after {settings.lint_timeout_seconds}s")
            logger.error("ansible-lint timeout", extra={"cmd": cmd})
        except Exception as e:
            ERROR_COUNT.inc()
            stderr = f"ansible-lint failed: {e}"
            logger.exception("ansible-lint error", extra={"cmd": cmd})
        finally:
            duration = time.time() - start
            REQUEST_LATENCY.labels(profile=profile).observe(duration)
            REQUEST_COUNT.labels(
                profile=profile,
                exit_code=str(exit_code),
            ).inc()
            shutil.rmtree(tmpdir, ignore_errors=True)

        return LintResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    return await run_in_threadpool(_invoke)

# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------
@app.post(
    "/v1/lint/{profile}",
    response_model=LintResult,
    summary="Lint a playbook",
    tags=["lint"],
    status_code=status.HTTP_200_OK,
)
async def lint_playbook(
    profile: Literal["basic", "production", "safe", "test", "minimal"],
    file: UploadFile = File(...),
):
    """
    Upload a .yml/.yaml playbook and lint it under the given profile.
    • exit_code=0 → no violations  
    • exit_code=2 → violations found  
    • exit_code=1 → internal error or timeout  
    """
    # Filename/type check
    if not file.filename.lower().endswith((".yml", ".yaml")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .yml/.yaml files are accepted."
        )

    body = await file.read()
    if len(body) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(f"File exceeds "
                    f"{settings.max_upload_size_bytes} bytes")
        )

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not valid UTF-8 text."
        )

    return await run_ansible_lint(text, profile)

@app.get(
    "/v1/profiles",
    response_model=ProfilesResponse,
    summary="List supported profiles",
    tags=["meta"],
)
def list_profiles():
    return ProfilesResponse(profiles=SUPPORTED_PROFILES)

# ------------------------------------------------------------------------------
# Uvicorn entrypoint
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        log_level=settings.log_level.lower(),
    )
