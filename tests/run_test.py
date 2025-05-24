#!/usr/bin/env python3
"""
Comprehensive test script for Ansible Lint MCP Server
Tests all endpoints, error conditions, and production scenarios
"""

import asyncio
import json
import time
import sys
import logging
from typing import Dict, Any, List
from dataclasses import dataclass
from contextlib import asynccontextmanager

import httpx
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mcp_test")

@dataclass
class TestResult:
    name: str
    passed: bool
    duration: float
    error: str = ""
    response: Dict[str, Any] = None

class MCPTester:
    def __init__(self, base_url: str = "http://localhost:8090"):
        self.base_url = base_url
        self.client = None
        self.results: List[TestResult] = []
        
    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def log_result(self, result: TestResult):
        self.results.append(result)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info(f"{status} {result.name} ({result.duration:.2f}s)")
        if not result.passed:
            logger.error(f"   Error: {result.error}")

    async def test_health_check(self) -> TestResult:
        """Test health endpoint"""
        start_time = time.time()
        try:
            resp = await self.client.get(f"{self.base_url}/health")
            duration = time.time() - start_time
            
            if resp.status_code in [200, 503]:  # 503 is acceptable if ansible-lint API is down
                data = resp.json()
                return TestResult("Health Check", True, duration, response=data)
            else:
                return TestResult("Health Check", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Health Check", False, duration, str(e))

    async def test_api_root(self) -> TestResult:
        """Test API root endpoint"""
        start_time = time.time()
        try:
            resp = await self.client.get(f"{self.base_url}/api/v1/")
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                if "available_tools" in data:
                    return TestResult("API Root", True, duration, response=data)
                else:
                    return TestResult("API Root", False, duration, "Missing available_tools")
            else:
                return TestResult("API Root", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("API Root", False, duration, str(e))

    async def test_get_lint_profiles(self) -> TestResult:
        """Test get_lint_profiles tool"""
        start_time = time.time()
        try:
            payload = {
                "tool_name": "get_lint_profiles",
                "inputs": {}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and "profiles" in data.get("output", {}):
                    return TestResult("Get Lint Profiles", True, duration, response=data)
                else:
                    return TestResult("Get Lint Profiles", False, duration, "Invalid response structure")
            else:
                return TestResult("Get Lint Profiles", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Get Lint Profiles", False, duration, str(e))

    async def test_validate_playbook_syntax_valid(self) -> TestResult:
        """Test validate_playbook_syntax with valid YAML"""
        start_time = time.time()
        valid_playbook = """---
- hosts: localhost
  tasks:
    - name: Test task
      debug:
        msg: "Hello World"
"""
        try:
            payload = {
                "tool_name": "validate_playbook_syntax",
                "inputs": {"playbook": valid_playbook}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("output", {}).get("valid"):
                    return TestResult("Validate Valid Playbook", True, duration, response=data)
                else:
                    return TestResult("Validate Valid Playbook", False, duration, "Should be valid")
            else:
                return TestResult("Validate Valid Playbook", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Validate Valid Playbook", False, duration, str(e))

    async def test_validate_playbook_syntax_invalid(self) -> TestResult:
        """Test validate_playbook_syntax with invalid YAML"""
        start_time = time.time()
        invalid_playbook = """---
- hosts: localhost
  tasks:
    - name: Test task
      debug:
        msg: "Hello World
        # Missing closing quote
"""
        try:
            payload = {
                "tool_name": "validate_playbook_syntax",
                "inputs": {"playbook": invalid_playbook}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("success") and not data.get("output", {}).get("valid"):
                    return TestResult("Validate Invalid Playbook", True, duration, response=data)
                else:
                    return TestResult("Validate Invalid Playbook", False, duration, "Should be invalid")
            else:
                return TestResult("Validate Invalid Playbook", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Validate Invalid Playbook", False, duration, str(e))

    async def test_lint_ansible_playbook(self) -> TestResult:
        """Test lint_ansible_playbook tool - Note: This may fail if ansible-lint API is not running"""
        start_time = time.time()
        test_playbook = """---
- hosts: localhost
  gather_facts: no
  tasks:
    - name: Test task
      debug:
        msg: "Hello World"
    
    - name: Another task
      shell: echo "test"
      changed_when: false
"""
        try:
            payload = {
                "tool_name": "lint_ansible_playbook",
                "inputs": {
                    "playbook": test_playbook,
                    "profile": "basic"
                }
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                # Check if we got a structured response (success or failure)
                if "output" in data and "tool" in data:
                    return TestResult("Lint Ansible Playbook", True, duration, response=data)
                else:
                    return TestResult("Lint Ansible Playbook", False, duration, "Invalid response structure")
            else:
                return TestResult("Lint Ansible Playbook", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Lint Ansible Playbook", False, duration, str(e))

    async def test_oversized_playbook(self) -> TestResult:
        """Test with oversized playbook (should be rejected)"""
        start_time = time.time()
        # Create a playbook larger than 1MB
        large_playbook = "---\n" + "# " + "x" * (1024 * 1024 + 100)
        
        try:
            payload = {
                "tool_name": "validate_playbook_syntax",
                "inputs": {"playbook": large_playbook}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("success") and "exceeds maximum size" in data.get("output", {}).get("error", ""):
                    return TestResult("Oversized Playbook Rejection", True, duration, response=data)
                else:
                    return TestResult("Oversized Playbook Rejection", False, duration, "Should reject large playbook")
            else:
                return TestResult("Oversized Playbook Rejection", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Oversized Playbook Rejection", False, duration, str(e))

    async def test_invalid_tool_name(self) -> TestResult:
        """Test with invalid tool name"""
        start_time = time.time()
        try:
            payload = {
                "tool_name": "nonexistent_tool",
                "inputs": {}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 404:
                data = resp.json()
                if "not found" in data.get("error", "").lower():
                    return TestResult("Invalid Tool Name", True, duration, response=data)
                else:
                    return TestResult("Invalid Tool Name", False, duration, "Wrong error message")
            else:
                return TestResult("Invalid Tool Name", False, duration, f"Expected 404, got {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Invalid Tool Name", False, duration, str(e))

    async def test_invalid_json(self) -> TestResult:
        """Test with invalid JSON payload"""
        start_time = time.time()
        try:
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                content="invalid json content",
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 400:
                return TestResult("Invalid JSON", True, duration)
            else:
                return TestResult("Invalid JSON", False, duration, f"Expected 400, got {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Invalid JSON", False, duration, str(e))

    async def test_concurrent_requests(self) -> TestResult:
        """Test concurrent request handling"""
        start_time = time.time()
        
        async def make_request(i: int):
            payload = {
                "tool_name": "get_lint_profiles", 
                "inputs": {}
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            return resp.status_code == 200
        
        try:
            # Make 5 concurrent requests
            tasks = [make_request(i) for i in range(5)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            duration = time.time() - start_time
            
            success_count = sum(1 for r in results if r is True)
            if success_count >= 4:  # Allow for some failures
                return TestResult("Concurrent Requests", True, duration, 
                                response={"successful": success_count, "total": 5})
            else:
                return TestResult("Concurrent Requests", False, duration, 
                                f"Only {success_count}/5 requests succeeded")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Concurrent Requests", False, duration, str(e))

    async def test_profile_validation(self) -> TestResult:
        """Test profile parameter validation"""
        start_time = time.time()
        test_playbook = "---\n- hosts: localhost\n  tasks: []"
        
        try:
            payload = {
                "tool_name": "lint_ansible_playbook",
                "inputs": {
                    "playbook": test_playbook,
                    "profile": "invalid_profile"  # Should default to "basic"
                }
            }
            resp = await self.client.post(
                f"{self.base_url}/api/v1/tool",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            duration = time.time() - start_time
            
            if resp.status_code == 200:
                data = resp.json()
                # The request should succeed (profile gets sanitized to "basic")
                return TestResult("Profile Validation", True, duration, response=data)
            else:
                return TestResult("Profile Validation", False, duration, f"Status: {resp.status_code}")
                
        except Exception as e:
            duration = time.time() - start_time
            return TestResult("Profile Validation", False, duration, str(e))

    async def run_all_tests(self):
        """Run all tests and generate report"""
        logger.info("🚀 Starting MCP Server Test Suite")
        logger.info(f"Testing server at: {self.base_url}")
        
        tests = [
            self.test_health_check,
            self.test_api_root,
            self.test_get_lint_profiles,
            self.test_validate_playbook_syntax_valid,
            self.test_validate_playbook_syntax_invalid,
            self.test_oversized_playbook,
            self.test_invalid_tool_name,
            self.test_invalid_json,
            self.test_concurrent_requests,
            self.test_profile_validation,
            self.test_lint_ansible_playbook,  # Run last as it may fail if ansible-lint API is down
        ]
        
        for test_func in tests:
            try:
                result = await test_func()
                self.log_result(result)
            except Exception as e:
                error_result = TestResult(test_func.__name__, False, 0, str(e))
                self.log_result(error_result)
            
            # Small delay between tests
            await asyncio.sleep(0.1)
        
        self.generate_report()

    def generate_report(self):
        """Generate test report"""
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.passed)
        failed_tests = total_tests - passed_tests
        
        logger.info("\n" + "="*60)
        logger.info("📊 TEST REPORT")
        logger.info("="*60)
        logger.info(f"Total Tests: {total_tests}")
        logger.info(f"Passed: {passed_tests}")
        logger.info(f"Failed: {failed_tests}")
        logger.info(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        logger.info("="*60)
        
        if failed_tests > 0:
            logger.info("\n❌ FAILED TESTS:")
            for result in self.results:
                if not result.passed:
                    logger.info(f"  - {result.name}: {result.error}")
        
        logger.info("\n📋 DETAILED RESULTS:")
        for result in self.results:
            status = "✅" if result.passed else "❌"
            logger.info(f"  {status} {result.name} ({result.duration:.2f}s)")
        
        # Save detailed report to file
        report_data = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "success_rate": (passed_tests/total_tests)*100,
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "duration": r.duration,
                    "error": r.error,
                    "response": r.response
                }
                for r in self.results
            ]
        }
        
        with open("mcp_test_report.json", "w") as f:
            json.dump(report_data, f, indent=2)
        
        logger.info(f"\n📄 Detailed report saved to: mcp_test_report.json")

async def main():
    """Main test runner"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test Ansible Lint MCP Server")
    parser.add_argument("--url", default="http://localhost:8090", 
                       help="Base URL of the MCP server")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose logging")
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    async with MCPTester(args.url) as tester:
        await tester.run_all_tests()
    
    # Exit with error code if any tests failed
    failed_count = sum(1 for r in tester.results if not r.passed)
    sys.exit(1 if failed_count > 0 else 0)

if __name__ == "__main__":
    asyncio.run(main())