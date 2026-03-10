import json
from app import create_app
import logging

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = create_app()

endpoints = []
for rule in app.url_map.iter_rules():
    if 'static' in rule.endpoint: continue
    if any(m in rule.methods for m in ['POST', 'PUT', 'DELETE', 'PATCH']):
        endpoints.append(rule.endpoint)

with open('endpoints.json', 'w') as f:
    json.dump(endpoints, f, indent=4)
