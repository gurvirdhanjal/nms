"""
Verify LDAP/AD auth flow against the local LDAP lab.

Prerequisite:
    docker compose -f docker-compose.ldap-lab.yml up -d

Usage:
    python tests/verify_ldap_lab.py
"""

import os
import sys
from pathlib import Path

from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.ldap_service import LDAPConnectionError, LDAPService


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _build_test_app():
    app = Flask(__name__)
    app.config.update(
        LDAP_ENABLED=_as_bool(os.environ.get("LDAP_ENABLED", "true"), True),
        LDAP_SERVER=os.environ.get("LDAP_SERVER", "ldap://127.0.0.1:389"),
        LDAP_BASE_DN=os.environ.get("LDAP_BASE_DN", "dc=example,dc=local"),
        LDAP_BIND_DN=os.environ.get("LDAP_BIND_DN", "cn=admin,dc=example,dc=local"),
        LDAP_BIND_PASSWORD=os.environ.get("LDAP_BIND_PASSWORD", "admin"),
        LDAP_USER_SEARCH_FILTER=os.environ.get("LDAP_USER_SEARCH_FILTER", "(uid={username})"),
        LDAP_USE_SSL=_as_bool(os.environ.get("LDAP_USE_SSL", "false")),
        LDAP_STARTTLS=_as_bool(os.environ.get("LDAP_STARTTLS", "false")),
        LDAP_TLS_VALIDATE=os.environ.get("LDAP_TLS_VALIDATE", "CERT_NONE"),
        LDAP_CA_CERT_FILE=os.environ.get("LDAP_CA_CERT_FILE", ""),
        LDAP_CONNECT_TIMEOUT=int(os.environ.get("LDAP_CONNECT_TIMEOUT", "5")),
        LDAP_RECEIVE_TIMEOUT=int(os.environ.get("LDAP_RECEIVE_TIMEOUT", "5")),
        LDAP_ATTR_EMAIL=os.environ.get("LDAP_ATTR_EMAIL", "mail"),
        LDAP_ATTR_DISPLAY_NAME=os.environ.get("LDAP_ATTR_DISPLAY_NAME", "displayName"),
        LDAP_ATTR_GUID=os.environ.get("LDAP_ATTR_GUID", "entryUUID"),
        LDAP_GROUP_SEARCH_BASE=os.environ.get(
            "LDAP_GROUP_SEARCH_BASE", "ou=groups,dc=example,dc=local"
        ),
        LDAP_GROUP_SEARCH_FILTER=os.environ.get(
            "LDAP_GROUP_SEARCH_FILTER", "(member={user_dn})"
        ),
        LDAP_DEFAULT_ROLE=os.environ.get("LDAP_DEFAULT_ROLE", "user"),
        LDAP_ADMIN_GROUP=os.environ.get(
            "LDAP_ADMIN_GROUP", "cn=MonitorAdmins,ou=groups,dc=example,dc=local"
        ),
    )
    return app


def _assert_login(username, password, expected_role):
    result = LDAPService.authenticate(username, password)
    if not result:
        raise AssertionError(f"Expected successful LDAP auth for '{username}', got failure.")

    role = result.get("role")
    if role != expected_role:
        raise AssertionError(
            f"Role mismatch for '{username}': expected '{expected_role}', got '{role}'."
        )

    print(
        f"[OK] username={username} role={role} "
        f"display_name={result.get('display_name')} email={result.get('email')}"
    )


def main():
    app = _build_test_app()

    with app.app_context():
        try:
            _assert_login("nmsadmin", "nmsadmin123", "admin")
            _assert_login("nmsuser", "nmsuser123", "user")

            bad = LDAPService.authenticate("nmsadmin", "wrong-password")
            if bad is not None:
                raise AssertionError("Invalid credentials should not authenticate.")
            print("[OK] Invalid credential check passed.")

        except LDAPConnectionError as exc:
            raise SystemExit(
                f"[FAIL] LDAP connection failed: {exc}. "
                f"Start lab with: docker compose -f docker-compose.ldap-lab.yml up -d"
            ) from exc

    print("[OK] LDAP lab verification passed.")


if __name__ == "__main__":
    main()
