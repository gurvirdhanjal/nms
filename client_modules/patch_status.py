"""
Best-effort OS package/update inventory, per the NMS backend's `patch_status`
contract. Tries the platform's primary package manager first, falls back to
a secondary one only when the primary tool isn't installed at all — never
raises, and returns [] if nothing could be collected.

Field shape per item:
  package_manager, package_name, installed_version, available_version,
  is_pending_update, last_checked_at

Note on scope: chocolatey is the only manager where both a full installed-list
command AND an outdated-check command were specified, so it's the only one
where up-to-date packages are reported (is_pending_update=False). For
winget/homebrew/apt/yum/dnf, only the documented "what needs updating" check
was specified — those managers only ever report pending-update packages here
(is_pending_update is always True for them). This keeps subprocess cost low,
matching the "limit subprocess cost" requirement; a full up-to-date inventory
for those managers would need additional, heavier commands not specified.
"""
import json
import platform
import re
import shutil
import subprocess
from datetime import datetime, timezone

_SUBPROCESS_TIMEOUT = 60


def _which(cmd):
    try:
        return shutil.which(cmd) is not None
    except Exception:
        return False


def _now_iso_z():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _run(args, timeout=_SUBPROCESS_TIMEOUT):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _collect_chocolatey():
    if not _which('choco'):
        return None  # not installed — caller tries the next manager

    installed = {}
    result = _run(['choco', 'list', '--local-only', '--no-color', '--limit-output'])
    for line in (result.stdout if result else '').splitlines():
        parts = line.strip().split('|')
        if len(parts) >= 2 and parts[0].strip():
            installed[parts[0].strip().lower()] = parts[1].strip()

    outdated = {}
    result = _run(['choco', 'outdated', '--no-color', '--limit-output'])
    for line in (result.stdout if result else '').splitlines():
        parts = line.strip().split('|')
        if len(parts) >= 3 and parts[0].strip():
            outdated[parts[0].strip().lower()] = parts[2].strip()

    now_iso = _now_iso_z()
    items = []
    for name, installed_version in installed.items():
        available_version = outdated.get(name)
        items.append({
            'package_manager': 'chocolatey',
            'package_name': name,
            'installed_version': installed_version,
            'available_version': available_version,
            'is_pending_update': available_version is not None,
            'last_checked_at': now_iso,
        })
    return items


def _parse_winget_table(output):
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    header_idx = None
    for idx, line in enumerate(lines):
        if re.search(r'\bName\b', line) and re.search(r'\bId\b', line) and re.search(r'\bVersion\b', line):
            header_idx = idx
            break
    if header_idx is None or header_idx + 1 >= len(lines):
        return []

    columns = re.split(r'\s{2,}', lines[header_idx].strip())
    try:
        name_idx = columns.index('Name')
        version_idx = columns.index('Version')
        available_idx = columns.index('Available')
    except ValueError:
        return []

    now_iso = _now_iso_z()
    items = []
    for line in lines[header_idx + 2:]:
        stripped = line.strip()
        if not stripped or set(stripped) == {'-'}:
            continue
        fields = re.split(r'\s{2,}', stripped)
        if len(fields) <= max(name_idx, version_idx, available_idx):
            continue
        name = fields[name_idx].strip()
        installed_version = fields[version_idx].strip()
        available_version = fields[available_idx].strip()
        if not name or not available_version or available_version.lower() == 'unknown':
            continue
        items.append({
            'package_manager': 'winget',
            'package_name': name,
            'installed_version': None if installed_version.lower() == 'unknown' else installed_version,
            'available_version': available_version,
            'is_pending_update': True,
            'last_checked_at': now_iso,
        })
    return items


def _collect_winget():
    if not _which('winget'):
        return None
    result = _run(['winget', 'upgrade', '--include-unknown'])
    if result is None:
        return []
    try:
        return _parse_winget_table(result.stdout or '')
    except Exception:
        return []


def _collect_homebrew():
    if platform.system() != 'Darwin' or not _which('brew'):
        return None
    result = _run(['brew', 'outdated', '--json=v2'])
    if result is None:
        return []
    try:
        data = json.loads(result.stdout or '{}')
    except Exception:
        return []

    now_iso = _now_iso_z()
    items = []
    for formula in (data.get('formulae') or []):
        name = formula.get('name')
        if not name:
            continue
        installed_versions = formula.get('installed_versions') or []
        items.append({
            'package_manager': 'homebrew',
            'package_name': name,
            'installed_version': installed_versions[0] if installed_versions else None,
            'available_version': formula.get('current_version'),
            'is_pending_update': True,
            'last_checked_at': now_iso,
        })
    return items


_APT_LINE_RE = re.compile(
    r'^(?P<pkg>[^/\s]+)/\S+\s+(?P<available>\S+)\s+\S+\s+\[upgradable from:\s*(?P<installed>[^\]]+)\]'
)


def _collect_apt():
    if platform.system() != 'Linux' or not _which('apt'):
        return None
    result = _run(['apt', 'list', '--upgradable'])
    if result is None:
        return []

    now_iso = _now_iso_z()
    items = []
    for line in (result.stdout or '').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('Listing'):
            continue
        match = _APT_LINE_RE.match(stripped)
        if not match:
            continue
        items.append({
            'package_manager': 'apt',
            'package_name': match.group('pkg'),
            'installed_version': match.group('installed').strip(),
            'available_version': match.group('available'),
            'is_pending_update': True,
            'last_checked_at': now_iso,
        })
    return items


def _collect_yum_dnf():
    if platform.system() != 'Linux':
        return None
    manager = 'dnf' if _which('dnf') else ('yum' if _which('yum') else None)
    if not manager:
        return None

    result = _run([manager, 'check-update', '--quiet'], timeout=90)
    if result is None or result.returncode not in (0, 100):
        return []

    now_iso = _now_iso_z()
    items = []
    for line in (result.stdout or '').splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        package_name = parts[0].rsplit('.', 1)[0]  # strip trailing .arch, e.g. "bash.x86_64"
        items.append({
            'package_manager': manager,
            'package_name': package_name,
            'installed_version': None,  # check-update doesn't report the installed version
            'available_version': parts[1],
            'is_pending_update': True,
            'last_checked_at': now_iso,
        })
    return items


def get_patch_status():
    """Best-effort package/update inventory for this host. Never raises."""
    system = platform.system()
    if system == 'Windows':
        collectors = [_collect_chocolatey, _collect_winget]
    elif system == 'Darwin':
        collectors = [_collect_homebrew]
    elif system == 'Linux':
        collectors = [_collect_apt, _collect_yum_dnf]
    else:
        collectors = []

    for collector in collectors:
        try:
            result = collector()
        except Exception:
            result = None
        if result is not None:
            return result
    return []
