
import sys
import os
import requests
import logging

# Add current directory to path
sys.path.append(os.getcwd())
logging.basicConfig(level=logging.ERROR)

from routes.tracking import NetworkScanner, _agent_http_get, AgentHttpError
import routes.tracking

# Mocking the _agent_http_get to simulate unreachable host
def mock_agent_http_get_fail(url, **kwargs):
    raise AgentHttpError("AGENT_UNREACHABLE", "Connection refused")

routes.tracking._agent_http_get = mock_agent_http_get_fail

scanner = NetworkScanner()

print("Testing fallback for unreachable agent (Host UP)...")
routes.tracking._ping_host = lambda ip, timeout=1.0: True # Simulate host UP

result = scanner.check_tracking_service("127.0.0.1", profile='interactive')
print(f"Result: {result['status']} (Expected: agent_missing_on_host)")

if result['status'] == 'agent_missing_on_host':
    print("SUCCESS: Detected agent_missing_on_host fallback!")
else:
    print("FAILURE: Failed to detect fallback for UP host.")

print("\nTesting fallback for unreachable agent (Host DOWN)...")
routes.tracking._ping_host = lambda ip, timeout=1.0: False # Simulate host DOWN
result = scanner.check_tracking_service("10.255.255.255", profile='interactive')
print(f"Result: {result['status']} (Expected: offline)")

if result['status'] == 'offline':
    print("SUCCESS: Detected offline status!")
else:
    print("FAILURE: Failed to detect offline status for DOWN host.")
