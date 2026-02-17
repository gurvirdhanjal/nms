import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app


def verify_sse_routes():
    print("Verifying SSE route registration and auth protection...")

    app = create_app({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
    })

    required_routes = ['/api/events/status', '/api/events/stream']
    registered_routes = {rule.rule for rule in app.url_map.iter_rules()}
    missing = [route for route in required_routes if route not in registered_routes]
    if missing:
        print(f"FAILED: Missing SSE route(s): {', '.join(missing)}")
        return 1

    with app.test_client() as client:
        for route in required_routes:
            response = client.get(route)
            if response.status_code != 401:
                print(
                    f"FAILED: {route} expected 401 Unauthorized, got {response.status_code}."
                )
                return 1

    print("SUCCESS: SSE routes are registered and protected (401 without session).")
    return 0


if __name__ == "__main__":
    raise SystemExit(verify_sse_routes())
