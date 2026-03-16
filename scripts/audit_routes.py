"""
scripts/audit_routes.py
-----------------------
Walks every file in routes/, finds every @<bp>.route decorator, and checks
whether that route is covered by an auth guard — either:
  - A blueprint-wide @<bp>.before_request that is also decorated with @require_login
  - A per-route @require_login / @require_role / @require_permission decorator

Output: printed table + scripts/audit_output.txt

Usage:
    python scripts/audit_routes.py
"""

import ast
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROUTES_DIR = Path(__file__).parent.parent / "routes"
OUTPUT_FILE = Path(__file__).parent / "audit_output.txt"

AUTH_DECORATOR_NAMES = {"require_login", "require_role", "require_permission", "login_required"}

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _decorator_name(node: ast.expr) -> str:
    """Return the bare name of a decorator (e.g. 'require_login', 'route', 'before_request')."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _decorator_full(node: ast.expr) -> str:
    """Return dotted string for an attribute decorator (e.g. 'devices_bp.before_request')."""
    if isinstance(node, ast.Attribute):
        return f"{ast.unparse(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _decorator_full(node.func)
    if isinstance(node, ast.Name):
        return node.id
    return ast.unparse(node)


def _extract_route(node: ast.Call) -> tuple[str, list[str]]:
    """Extract (path, methods) from a @bp.route(...) call node."""
    path = "?"
    methods = ["GET"]
    if node.args:
        try:
            path = ast.literal_eval(node.args[0])
        except Exception:
            path = ast.unparse(node.args[0])
    for kw in node.keywords:
        if kw.arg == "methods":
            try:
                methods = [m.upper() for m in ast.literal_eval(kw.value)]
            except Exception:
                methods = ["?"]
    return path, methods


def _extract_role(node: ast.Call) -> str:
    """Extract role arg(s) from @require_role('admin') → 'admin'."""
    parts = []
    for arg in node.args:
        try:
            parts.append(str(ast.literal_eval(arg)))
        except Exception:
            parts.append(ast.unparse(arg))
    for kw in node.keywords:
        try:
            parts.append(f"{kw.arg}={ast.literal_eval(kw.value)}")
        except Exception:
            parts.append(f"{kw.arg}={ast.unparse(kw.value)}")
    return ", ".join(parts) if parts else "any"


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------

def analyse_file(path: Path) -> list[dict]:
    """
    Parse one route file and return a list of route records:
      {file, route_path, methods, protected, role}
    """
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"  [SKIP] Syntax error in {path.name}: {exc}", file=sys.stderr)
        return []

    # ---- Step 1: detect blueprint-wide before_request auth guard -------
    # A function decorated with BOTH @<bp>.before_request AND @require_login
    # (or @require_role / @require_permission) guards every route in the file.
    bp_wide_protected = False
    bp_wide_role = ""

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dec_names = [_decorator_name(d) for d in node.decorator_list]
        if "before_request" not in dec_names:
            continue
        # This is a before_request handler — check if it has an auth decorator
        for dec in node.decorator_list:
            name = _decorator_name(dec)
            if name in AUTH_DECORATOR_NAMES:
                bp_wide_protected = True
                if name == "require_role" and isinstance(dec, ast.Call):
                    bp_wide_role = _extract_role(dec)
                else:
                    bp_wide_role = ""
                break

    # ---- Step 2: collect every route-decorated function ----------------
    records = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        route_paths: list[tuple[str, list[str]]] = []
        per_route_protected = False
        per_route_role = ""

        for dec in node.decorator_list:
            name = _decorator_name(dec)

            # Route decorator
            if name == "route" and isinstance(dec, ast.Call):
                route_path, methods = _extract_route(dec)
                route_paths.append((route_path, methods))

            # Auth decorators
            elif name in AUTH_DECORATOR_NAMES:
                per_route_protected = True
                if name == "require_role" and isinstance(dec, ast.Call):
                    per_route_role = _extract_role(dec)
                elif name == "require_permission" and isinstance(dec, ast.Call):
                    per_route_role = f"perm:{_extract_role(dec)}"
                else:
                    per_route_role = ""

        if not route_paths:
            continue  # not a route handler

        protected = bp_wide_protected or per_route_protected

        # Build role string for display
        if per_route_protected and per_route_role:
            role_display = per_route_role
        elif per_route_protected:
            role_display = "login"
        elif bp_wide_protected:
            role_display = f"bp:{bp_wide_role}" if bp_wide_role else "bp:login"
        else:
            role_display = ""

        for route_path, methods in route_paths:
            records.append(
                {
                    "file": path.name,
                    "route_path": route_path,
                    "methods": ", ".join(methods),
                    "protected": "YES" if protected else "NO",
                    "role": role_display,
                }
            )

    return records


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _col_widths(rows: list[dict], headers: list[str]) -> dict[str, int]:
    widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            widths[h] = max(widths[h], len(str(row.get(h, ""))))
    return widths


def format_table(rows: list[dict], headers: list[str]) -> str:
    if not rows:
        return "(no routes found)\n"
    w = _col_widths(rows, headers)
    sep = "+-" + "-+-".join("-" * w[h] for h in headers) + "-+"
    hdr = "| " + " | ".join(h.ljust(w[h]) for h in headers) + " |"
    lines = [sep, hdr, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")).ljust(w[h]) for h in headers) + " |")
    lines.append(sep)
    return "\n".join(lines)


def format_summary(rows: list[dict]) -> str:
    total = len(rows)
    unprotected = [r for r in rows if r["protected"] == "NO"]
    protected = total - len(unprotected)
    lines = [
        "",
        f"  Total routes   : {total}",
        f"  Protected      : {protected}",
        f"  UNPROTECTED    : {len(unprotected)}",
        "",
    ]
    if unprotected:
        lines.append("  Unprotected routes:")
        for r in unprotected:
            lines.append(f"    {r['file']:<35} {r['methods']:<18} {r['route_path']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    headers = ["file", "route_path", "methods", "protected", "role"]

    # Collect all route files (skip __init__.py and subdirectories)
    route_files = sorted(
        p for p in ROUTES_DIR.iterdir()
        if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"
    )
    # Also pick up api_v1 subdirectory if present
    api_v1_dir = ROUTES_DIR / "api_v1"
    if api_v1_dir.is_dir():
        route_files += sorted(
            p for p in api_v1_dir.rglob("*.py") if p.name != "__init__.py"
        )

    all_rows: list[dict] = []
    for path in route_files:
        all_rows.extend(analyse_file(path))

    # Sort: unprotected first, then by file name, then by route path
    all_rows.sort(key=lambda r: (r["protected"] == "YES", r["file"], r["route_path"]))

    table = format_table(all_rows, headers)
    summary = format_summary(all_rows)

    header_block = (
        "=" * 80 + "\n" +
        "Route Auth Audit — Device Monitoring Tactical\n" +
        "=" * 80 + "\n"
    )

    sep80 = "=" * 80
    output = header_block + "\n" + table + "\n" + sep80 + "\nSUMMARY\n" + sep80 + "\n" + summary

    print(output)

    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"\n  Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
