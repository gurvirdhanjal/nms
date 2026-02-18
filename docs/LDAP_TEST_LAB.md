# LDAP / AD Test Lab (No Real AD Required)

This repo includes a local LDAP lab so you can validate authentication flow before connecting to a real Active Directory.

## 1. Start the lab

```powershell
docker compose -f docker-compose.ldap-lab.yml up -d
```

Services:
- LDAP server: `ldap://127.0.0.1:389`
- phpLDAPadmin UI: `http://127.0.0.1:8085`

phpLDAPadmin login:
- Login DN: `cn=admin,dc=example,dc=local`
- Password: `admin`

## 2. Test users seeded in lab

- Admin user:
  - Username: `nmsadmin`
  - Password: `nmsadmin123`
  - Group: `cn=MonitorAdmins,ou=groups,dc=example,dc=local`
- Normal user:
  - Username: `nmsuser`
  - Password: `nmsuser123`
  - Group: `cn=MonitorUsers,ou=groups,dc=example,dc=local`

## 3. App config for this lab

Copy values from:
- `tools/ldap_lab/ldap_lab.env.example`

Important defaults for OpenLDAP lab:
- `LDAP_USER_SEARCH_FILTER=(uid={username})`
- `LDAP_ATTR_GUID=entryUUID`
- `LDAP_GROUP_SEARCH_BASE=ou=groups,dc=example,dc=local`
- `LDAP_GROUP_SEARCH_FILTER=(member={user_dn})`

## 4. Verify auth without running full app

```powershell
python tests/verify_ldap_lab.py
```

Expected:
- `nmsadmin` authenticates as role `admin`
- `nmsuser` authenticates as role `user`
- Invalid password is rejected

## 5. Verify through login page

1. Start app normally.
2. Enable LDAP settings in your `.env`.
3. Login with:
   - `nmsadmin` / `nmsadmin123`
   - `nmsuser` / `nmsuser123`
4. Confirm:
   - Users are upserted in `user` table with `auth_source='ldap'`.
   - Admin role is assigned by LDAP group mapping.

## 6. Stop and cleanup

```powershell
docker compose -f docker-compose.ldap-lab.yml down -v
```
