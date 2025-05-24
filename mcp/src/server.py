import sys
import os
import json
import time
import logging
import asyncio
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

import httpx
import yaml

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount
from starlette.middleware.cors import CORSMiddleware

from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport

# ─────────────── Config & Globals ───────────────
ANSIBLE_LINT_API = "http://localhost:8080/v1"
SUPPORTED_PROFILES = ["basic", "production", "safety", "test", "minimal"]
MAX_CONCURRENT_REQUESTS = 10
REQUEST_TIMEOUT = 60.0
MAX_PLAYBOOK_SIZE = 1024 * 1024  # 1MB limit

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ansible-lint-mcp")

# Connection pool for HTTP client
http_client: Optional[httpx.AsyncClient] = None
semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

# ─────────────── HTTP Client Management ───────────────
@asynccontextmanager
async def get_http_client():
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
        )
    try:
        yield http_client
    except Exception as e:
        logger.error(f"HTTP client error: {e}")
        raise

async def close_http_client():
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None

# ─────────────── Helpers ───────────────
def wrap_tool_output(tool_name: str, payload: Any, success: bool = True) -> str:
    return json.dumps({
        "tool": tool_name,
        "success": success,
        "output": payload,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    }, indent=2)

def validate_playbook_content(playbook: str) -> tuple[bool, str]:
    """Validate playbook content and size"""
    if len(playbook.encode('utf-8')) > MAX_PLAYBOOK_SIZE:
        return False, f"Playbook exceeds maximum size of {MAX_PLAYBOOK_SIZE} bytes"
    
    try:
        # Basic YAML validation
        yaml.safe_load(playbook)
        return True, ""
    except yaml.YAMLError as e:
        return False, f"Invalid YAML format: {str(e)}"

def sanitize_profile(profile: str) -> str:
    """Sanitize and validate profile parameter"""
    profile = profile.strip().lower()
    if profile not in SUPPORTED_PROFILES:
        logger.warning(f"Invalid profile '{profile}', using 'basic'")
        return "basic"
    return profile

def format_lint_output(result: Dict[str, Any]) -> Dict[str, Any]:
    """Format lint output for LLM consumption"""
    formatted = {
        "summary": {
            "exit_code": result.get("exit_code", -1),
            "passed": result.get("exit_code", -1) == 0,
            "profile_used": result.get("profile", "unknown")
        },
        "issues": [],
        "raw_output": {
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", "")
        }
    }
    
    # Parse stdout for structured issues if possible
    stdout = result.get("stdout", "")
    if stdout:
        # Try to extract structured information from ansible-lint output
        lines = stdout.split('\n')
        issues = []
        current_issue = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Look for issue patterns (this may need adjustment based on your ansible-lint version)
            if line.startswith('WARNING') or line.startswith('ERROR'):
                if current_issue:
                    issues.append(current_issue)
                current_issue = {
                    "severity": "warning" if line.startswith('WARNING') else "error",
                    "message": line,
                    "details": []
                }
            elif current_issue and line.startswith(' '):
                current_issue["details"].append(line.strip())
        
        if current_issue:
            issues.append(current_issue)
        
        formatted["issues"] = issues
        formatted["summary"]["issue_count"] = len(issues)
        formatted["summary"]["error_count"] = len([i for i in issues if i["severity"] == "error"])
        formatted["summary"]["warning_count"] = len([i for i in issues if i["severity"] == "warning"])
    
    return formatted

# ─────────────── MCP Server & Tools ───────────────
mcp = FastMCP("Ansible Lint MCP Server", dependencies=["httpx", "pyyaml"])
sse = SseServerTransport("/messages/")

@mcp.tool(name="lint_ansible_playbook", description="Run ansible-lint and return structured report suitable for LLM analysis")
async def lint_ansible_playbook(playbook: str, profile: str = "basic") -> str:
    """Lint an Ansible playbook and return a structured report"""
    
    # Validate inputs
    is_valid, error_msg = validate_playbook_content(playbook)
    if not is_valid:
        return wrap_tool_output("lint_ansible_playbook", {"error": error_msg}, success=False)
    
    profile = sanitize_profile(profile)
    url = f"{ANSIBLE_LINT_API}/lint/{profile}"
    
    async with semaphore:  # Rate limiting
        try:
            async with get_http_client() as client:
                files = {"file": ("playbook.yml", playbook.encode('utf-8'))}
                
                logger.info(f"Starting lint request with profile: {profile}")
                resp = await client.post(url, files=files)
                resp.raise_for_status()
                result = resp.json()
                
                # Add profile info to result
                result["profile"] = profile
                
                # Format output for LLM consumption
                formatted_result = format_lint_output(result)
                
                logger.info(f"Lint completed successfully, exit_code: {result.get('exit_code', 'unknown')}")
                return wrap_tool_output("lint_ansible_playbook", formatted_result)
                
        except httpx.TimeoutException:
            error_msg = f"Lint request timed out after {REQUEST_TIMEOUT}s"
            logger.error(error_msg)
            return wrap_tool_output("lint_ansible_playbook", {"error": error_msg}, success=False)
        except httpx.HTTPStatusError as e:
            error_msg = f"Lint API returned {e.response.status_code}: {e.response.text}"
            logger.error(error_msg)
            return wrap_tool_output("lint_ansible_playbook", {"error": error_msg}, success=False)
        except Exception as e:
            error_msg = f"Unexpected error during linting: {str(e)}"
            logger.error(error_msg)
            return wrap_tool_output("lint_ansible_playbook", {"error": error_msg}, success=False)

@mcp.tool(name="get_lint_profiles", description="Get supported ansible-lint profiles and their descriptions")
async def get_lint_profiles() -> str:
    """Get available lint profiles"""
    profiles_info = {
        "basic": "Basic rule set for general use",
        "production": "Strict rules for production environments", 
        "safe": "Conservative rules that avoid false positives",
        "test": "Rules optimized for test playbooks",
        "minimal": "Minimal rule set for quick checks"
    }
    
    result = {
        "profiles": SUPPORTED_PROFILES,
        "profile_descriptions": profiles_info,
        "default_profile": "basic"
    }
    
    return wrap_tool_output("get_lint_profiles", result)

@mcp.tool(name="validate_playbook_syntax", description="Validate Ansible playbook YAML syntax without full linting")
async def validate_playbook_syntax(playbook: str) -> str:
    """Quick syntax validation for playbooks"""
    is_valid, error_msg = validate_playbook_content(playbook)
    
    result = {
        "valid": is_valid,
        "error": error_msg if not is_valid else None,
        "size_bytes": len(playbook.encode('utf-8')),
        "max_size_bytes": MAX_PLAYBOOK_SIZE
    }
    
    return wrap_tool_output("validate_playbook_syntax", result, success=is_valid)

@mcp.tool(name="lint_playbook_stream", description="Run lint with progress updates (for long-running operations)")
async def lint_playbook_stream(playbook: str, profile: str = "basic") -> str:
    """Lint with streaming progress updates"""
    job_id = f"lint-job-{int(time.time())}-{hash(playbook) & 0x7fffffff}"
    
    # Validate inputs first
    is_valid, error_msg = validate_playbook_content(playbook)
    if not is_valid:
        sse.send_message({
            "event": "lint-status",
            "data": {"job_id": job_id, "status": "ERROR", "error": error_msg}
        })
        return wrap_tool_output("lint_playbook_stream", {"error": error_msg}, success=False)
    
    profile = sanitize_profile(profile)
    
    sse.send_message({
        "event": "lint-status", 
        "data": {"job_id": job_id, "status": "STARTED", "profile": profile}
    })

    async with semaphore:
        try:
            # Simulate progress steps
            for i in range(1, 4):
                await asyncio.sleep(0.5)  # Non-blocking sleep
                sse.send_message({
                    "event": "lint-status",
                    "data": {"job_id": job_id, "status": f"Processing step {i}/3"}
                })

            url = f"{ANSIBLE_LINT_API}/lint/{profile}"
            async with get_http_client() as client:
                files = {"file": ("playbook.yml", playbook.encode('utf-8'))}
                resp = await client.post(url, files=files)
                resp.raise_for_status()
                result = resp.json()
                result["profile"] = profile

            formatted_result = format_lint_output(result)
            
            sse.send_message({
                "event": "lint-status",
                "data": {"job_id": job_id, "status": "COMPLETED", "result": formatted_result}
            })

            return wrap_tool_output("lint_playbook_stream", {
                "job_id": job_id,
                **formatted_result
            })

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Streaming lint error: {error_msg}")
            sse.send_message({
                "event": "lint-status",
                "data": {"job_id": job_id, "status": "ERROR", "error": error_msg}
            })
            return wrap_tool_output("lint_playbook_stream", {"error": error_msg}, success=False)

# Store tool functions for manual access
TOOL_FUNCTIONS = {
    "lint_ansible_playbook": lint_ansible_playbook,
    "get_lint_profiles": get_lint_profiles,
    "validate_playbook_syntax": validate_playbook_syntax,
    "lint_playbook_stream": lint_playbook_stream,
}

# ─────────────── API Routes ───────────────
async def api_root(request: Request):
    return JSONResponse({
        "status": "ok",
        "service": "Ansible Lint MCP Server",
        "available_tools": list(TOOL_FUNCTIONS.keys()),
        "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
        "request_timeout": REQUEST_TIMEOUT
    })

async def health_check(request: Request):
    """Health check endpoint"""
    try:
        # Test connection to ansible-lint API
        async with get_http_client() as client:
            resp = await client.get(f"{ANSIBLE_LINT_API}/health", timeout=5.0)
            api_healthy = resp.status_code == 200
    except:
        api_healthy = False
    
    status = "healthy" if api_healthy else "degraded" 
    return JSONResponse({
        "status": status,
        "ansible_lint_api": api_healthy,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    }, status_code=200 if api_healthy else 503)

async def tool_route(request: Request):
    try:
        body = await request.json()
        tool_name = body.get("tool_name")
        inputs = body.get("inputs", {})
        
        if not tool_name:
            return JSONResponse({"error": "Missing tool_name parameter"}, status_code=400)
        
        if tool_name not in TOOL_FUNCTIONS:
            return JSONResponse({
                "error": f"Tool '{tool_name}' not found",
                "available_tools": list(TOOL_FUNCTIONS.keys())
            }, status_code=404)
        
        # Call the tool function directly
        logger.info(f"Executing tool: {tool_name}")
        result = await TOOL_FUNCTIONS[tool_name](**inputs)
        return JSONResponse(json.loads(result))
        
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON in request body"}, status_code=400)
    except TypeError as e:
        return JSONResponse({"error": f"Invalid parameters: {str(e)}"}, status_code=400)
    except Exception as e:
        logger.error(f"Error in tool_route: {e}")
        return JSONResponse({"error": f"Internal server error: {str(e)}"}, status_code=500)

async def handle_sse(request: Request):
    try:
        async with sse.connect_sse(request.scope, request.receive, request._send) as (r, w):
            await mcp._mcp_server.run(r, w, mcp._mcp_server.create_initialization_options())
    except Exception as e:
        logger.error(f"SSE error: {e}")

# ─────────────── Application Lifecycle ───────────────
async def startup():
    logger.info("Starting Ansible Lint MCP Server...")

async def shutdown():
    logger.info("Shutting down Ansible Lint MCP Server...")
    await close_http_client()

# ─────────────── Starlette App ───────────────
app = Starlette(
    debug=False,  # Set to False for production
    routes=[
        Route("/", api_root),
        Route("/health", health_check),
        Route("/sse", handle_sse),
        Route("/api/v1/", api_root),
        Route("/api/v1/tool", tool_route, methods=["POST"]),
        Mount("/messages/", app=sse.handle_post_message),
    ],
    on_startup=[startup],
    on_shutdown=[shutdown]
)

# Add CORS middleware for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8090,
        log_level="info",
        access_log=True
    )