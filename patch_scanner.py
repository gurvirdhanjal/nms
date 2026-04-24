import re

with open("services/network_scanner.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = '''        try:
            url = f"http://{ip}:5002/api/identity"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    return json.loads(response.read().decode())'''

new_code = '''        try:
            import time
            start_time = time.time()
            url = f"http://{ip}:5002/api/identity"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode())
                    data["http_latency_ms"] = (time.time() - start_time) * 1000.0
                    return data'''

text = text.replace(old_code, new_code)
with open("services/network_scanner.py", "w", encoding="utf-8") as f:
    f.write(text)
print("network_scanner.py patched")
