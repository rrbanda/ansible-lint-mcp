from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
import subprocess
import tempfile
import os
import logging
from typing import Literal

app = FastAPI(title="Ansible Lint Service", version="1.0.0")

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ansible-lint-service")

SUPPORTED_PROFILES = ["basic", "production", "safe", "test", "minimal"]

def run_ansible_lint(playbook_text: str, profile: str) -> dict:
    """Run ansible-lint with given playbook content and profile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        playbook_path = os.path.join(tmpdir, "playbook.yml")
        with open(playbook_path, "w") as f:
            f.write(playbook_text)

        cmd = ["ansible-lint", "--nocolor", "--profile", profile, playbook_path]
        logger.info(f"Running: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }

@app.post("/lint/{profile}")
async def lint_playbook(
    profile: Literal["basic", "production", "safe", "test", "minimal"],
    file: UploadFile = File(...)
):
    """
    Lint an Ansible playbook using a specified profile.
    Upload a .yml file and select a profile.
    """
    if profile not in SUPPORTED_PROFILES:
        raise HTTPException(status_code=400, detail=f"Unsupported profile: {profile}")

    try:
        playbook_text = (await file.read()).decode("utf-8")
        result = run_ansible_lint(playbook_text, profile)
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Ansible Lint failed")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/profiles")
def get_profiles():
    """List supported ansible-lint profiles."""
    return {"profiles": SUPPORTED_PROFILES}

@app.get("/health")
def health_check():
    return {"status": "ok"}
