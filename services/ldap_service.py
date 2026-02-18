"""
LDAP / Active Directory Authentication Service.

Flow: service-account bind -> search user DN -> user-bind with supplied password.
Falls back gracefully on connection errors so local auth remains available.
"""

import logging
import ssl

from flask import current_app
from ldap3 import ALL, SUBTREE, SIMPLE, Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPBindError,
    LDAPExceptionError,
    LDAPInvalidCredentialsResult,
    LDAPSocketOpenError,
    LDAPSocketReceiveError,
)
from ldap3.utils.conv import escape_filter_chars

log = logging.getLogger(__name__)


class LDAPService:
    """Handles LDAP authentication with security hardening."""

    @staticmethod
    def _build_tls():
        """Build TLS configuration from app config."""
        validate_map = {
            'CERT_NONE': ssl.CERT_NONE,
            'CERT_OPTIONAL': ssl.CERT_OPTIONAL,
            'CERT_REQUIRED': ssl.CERT_REQUIRED,
        }
        validate = validate_map.get(
            current_app.config.get('LDAP_TLS_VALIDATE', 'CERT_REQUIRED'),
            ssl.CERT_REQUIRED,
        )
        ca_file = current_app.config.get('LDAP_CA_CERT_FILE') or None

        return Tls(
            validate=validate,
            ca_certs_file=ca_file if ca_file else None,
            version=ssl.PROTOCOL_TLS_CLIENT if validate != ssl.CERT_NONE else ssl.PROTOCOL_TLS,
        )

    @staticmethod
    def _get_server():
        """Create an LDAP Server object from app config."""
        use_ssl = current_app.config.get('LDAP_USE_SSL', False)
        tls_obj = LDAPService._build_tls() if (use_ssl or current_app.config.get('LDAP_STARTTLS')) else None

        return Server(
            current_app.config['LDAP_SERVER'],
            use_ssl=use_ssl,
            tls=tls_obj,
            get_info=ALL,
            connect_timeout=current_app.config.get('LDAP_CONNECT_TIMEOUT', 5),
        )

    @staticmethod
    def _safe_unbind(connection):
        if not connection:
            return
        try:
            connection.unbind()
        except Exception:
            pass

    @classmethod
    def authenticate(cls, username, password):
        """
        Authenticate a user against LDAP/AD.

        Returns dict on success:
            {
                'dn': str,
                'email': str | None,
                'display_name': str | None,
                'external_id': str | None,
                'groups': list[str],
                'role': str
            }
        Returns None on invalid credentials.
        Raises LDAPConnectionError on infrastructure failures.
        """
        if not current_app.config.get('LDAP_ENABLED'):
            return None

        if not username or not password:
            return None

        server_url = current_app.config.get('LDAP_SERVER', '')
        if not server_url:
            log.warning("[LDAP] LDAP_ENABLED=true but LDAP_SERVER is empty. Skipping.")
            return None

        svc_conn = None
        user_conn = None

        try:
            server = cls._get_server()
            timeout = current_app.config.get('LDAP_RECEIVE_TIMEOUT', 5)

            bind_dn = current_app.config.get('LDAP_BIND_DN', '')
            bind_pw = current_app.config.get('LDAP_BIND_PASSWORD', '')

            svc_conn = Connection(
                server,
                user=bind_dn,
                password=bind_pw,
                authentication=SIMPLE,
                read_only=True,
                receive_timeout=timeout,
                raise_exceptions=True,
            )

            if current_app.config.get('LDAP_STARTTLS') and not current_app.config.get('LDAP_USE_SSL'):
                svc_conn.open()
                svc_conn.start_tls()
            svc_conn.bind()

            safe_username = escape_filter_chars(username)
            search_filter = current_app.config.get(
                'LDAP_USER_SEARCH_FILTER',
                '(sAMAccountName={username})',
            ).replace('{username}', safe_username)

            base_dn = current_app.config.get('LDAP_BASE_DN', '')
            attr_email = current_app.config.get('LDAP_ATTR_EMAIL', 'mail')
            attr_display = current_app.config.get('LDAP_ATTR_DISPLAY_NAME', 'displayName')
            attr_guid = current_app.config.get('LDAP_ATTR_GUID', 'objectGUID')

            svc_conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=[attr_email, attr_display, attr_guid, 'memberOf'],
            )

            if not svc_conn.entries:
                log.info(f"[LDAP] User '{username}' not found in directory.")
                return None

            entry = svc_conn.entries[0]
            user_dn = entry.entry_dn

            user_conn = Connection(
                server,
                user=user_dn,
                password=password,
                authentication=SIMPLE,
                read_only=True,
                receive_timeout=timeout,
                raise_exceptions=True,
            )

            if current_app.config.get('LDAP_STARTTLS') and not current_app.config.get('LDAP_USE_SSL'):
                user_conn.open()
                user_conn.start_tls()
            user_conn.bind()

            email = str(entry[attr_email]) if hasattr(entry, attr_email) and entry[attr_email].value else None
            display_name = (
                str(entry[attr_display])
                if hasattr(entry, attr_display) and entry[attr_display].value
                else None
            )
            external_id = str(entry[attr_guid]) if hasattr(entry, attr_guid) and entry[attr_guid].value else None

            groups = []
            if hasattr(entry, 'memberOf') and entry['memberOf'].values:
                groups = [str(group_dn) for group_dn in entry['memberOf'].values]

            # OpenLDAP labs often do not expose memberOf by default.
            if not groups:
                group_base = current_app.config.get('LDAP_GROUP_SEARCH_BASE') or base_dn
                group_filter_template = current_app.config.get(
                    'LDAP_GROUP_SEARCH_FILTER',
                    '(|(member={user_dn})(uniqueMember={user_dn})(memberUid={username}))',
                )
                if group_base and group_filter_template:
                    group_filter = (
                        group_filter_template
                        .replace('{user_dn}', escape_filter_chars(user_dn))
                        .replace('{username}', safe_username)
                    )
                    svc_conn.search(
                        search_base=group_base,
                        search_filter=group_filter,
                        search_scope=SUBTREE,
                        attributes=['cn'],
                    )
                    groups = [str(group_entry.entry_dn) for group_entry in svc_conn.entries]

            role = current_app.config.get('LDAP_DEFAULT_ROLE', 'user')
            admin_group = current_app.config.get('LDAP_ADMIN_GROUP', '')
            if admin_group:
                admin_group_lower = admin_group.lower()
                if any(admin_group_lower in group_dn.lower() for group_dn in groups):
                    role = 'admin'

            log.info(f"[LDAP] Authenticated '{username}' (role={role})")
            return {
                'dn': user_dn,
                'email': email,
                'display_name': display_name,
                'external_id': external_id,
                'groups': groups,
                'role': role,
            }

        except (LDAPBindError, LDAPInvalidCredentialsResult):
            log.info(f"[LDAP] Invalid credentials for '{username}'.")
            return None
        except (LDAPSocketOpenError, LDAPSocketReceiveError) as exc:
            log.warning(f"[LDAP] Connection error: {exc}. Falling back to local auth.")
            raise LDAPConnectionError(str(exc))
        except LDAPExceptionError as exc:
            log.warning(f"[LDAP] Unexpected error: {exc}. Falling back to local auth.")
            raise LDAPConnectionError(str(exc))
        finally:
            cls._safe_unbind(user_conn)
            cls._safe_unbind(svc_conn)


class LDAPConnectionError(Exception):
    """Raised when LDAP server is unreachable. Caller should fall through to local auth."""
