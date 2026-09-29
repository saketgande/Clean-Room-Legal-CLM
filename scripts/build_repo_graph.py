#!/usr/bin/env python3
"""Static scanner that builds the repo knowledge graph consumed by subagents
during /plan and /implement.

Backend (backend/app/**) is parsed with the stdlib `ast` module: real parsing,
zero new dependencies. Frontend (frontend/src/lib/endpoints.ts, types.ts,
components/ui.tsx) has no stdlib TS parser available, so it's scanned with a
small depth-aware lexer (tracks brace depth + string/template/comment mode)
rather than naive per-line regex, because endpoints.ts methods routinely span
multiple lines (e.g. building a FormData before the apiFetch() call).

Output (all under specs/_graph/, all generated -- do not hand-edit):
  repo-graph.json      machine-readable source of truth (nodes, edges, warnings)
  index.md             cross-cutting facts + domain table (what every agent reads)
  domains/<domain>.md  one file per backend domain (what a task's agent reads)

domain-aliases.json (hand-maintained, NOT generated) seeds backend<->frontend
name pairs the naming heuristic can't resolve on its own.

Usage: python scripts/build_repo_graph.py [--root PATH]
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPT_DIR.parent

MIXIN_NAMES = {
    "IdMixin",
    "TimestampMixin",
    "OrgScopedMixin",
    "ActorTrackedMixin",
    "SoftDeleteMixin",
    "TableNameMixin",
}

HTTP_VERBS = {"get", "post", "put", "patch", "delete"}

# Backend delegation ownership, mirrored from .claude/CLAUDE.md's table.
FILE_ROLE_OWNER = {
    "models.py": "db-engineer",
    "routes.py": "backend-dev",
    "service.py": "backend-dev",
    "schemas.py": "backend-dev",
    "access.py": "backend-dev",
}

NON_SCHEMA_ANNOTATIONS = {
    "Session",
    "Request",
    "UploadFile",
    "str",
    "int",
    "float",
    "bool",
    "bytes",
    "None",
}
NON_SCHEMA_ARG_NAMES = {"db", "current_user", "self", "request"}


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def camel_to_snake_singular(name: str) -> str:
    """Mirrors TableNameMixin.__tablename__ in backend/app/core/database.py exactly."""
    chars: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)


def callable_name(func_node: ast.expr) -> str | None:
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def rel(path: Path, root: Path) -> str:
    """Relative path as a forward-slash string, regardless of host OS."""
    return path.relative_to(root).as_posix()


def norm_key(s: str) -> str:
    """Normalize a domain/group name for fuzzy pairing: lowercase, strip
    separators and a trailing 'api' suffix."""
    s = s.lower().replace("_", "").replace("-", "")
    if s.endswith("api"):
        s = s[:-3]
    return s


# --------------------------------------------------------------------------
# Backend extraction
# --------------------------------------------------------------------------


def find_backend_domains(backend_app: Path) -> list[Path]:
    domains = []
    for child in sorted(backend_app.iterdir()):
        if not child.is_dir() or child.name == "__pycache__":
            continue
        domains.append(child)
    return domains


def extract_models(path: Path, root: Path, domain: str, warnings: list[str]) -> tuple[list[dict], list[dict]]:
    """Returns (model_nodes, fk_edges)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        warnings.append(f"models.py parse error in {path}: {exc}")
        return [], []

    models: list[dict] = []
    fk_edges: list[dict] = []

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = [callable_name(b) or unparse(b) or "?" for b in node.bases]
        mixins = [b for b in base_names if b in MIXIN_NAMES]
        table_name = camel_to_snake_singular(node.name)
        fk_columns: list[dict] = []

        for stmt in node.body:
            explicit_table = None
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "__tablename__"
                and isinstance(stmt.value, ast.Constant)
            ):
                explicit_table = stmt.value.value
            if explicit_table:
                table_name = explicit_table

            if not (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Call)
                and callable_name(stmt.value.func) == "Column"
            ):
                continue
            column_name = stmt.targets[0].id
            for sub in ast.walk(stmt.value):
                if isinstance(sub, ast.Call) and callable_name(sub.func) == "ForeignKey":
                    if sub.args and isinstance(sub.args[0], ast.Constant):
                        fk_columns.append({"column": column_name, "ref": sub.args[0].value})
                    break

        model_id = f"model:{domain}:{node.name}"
        models.append(
            {
                "id": model_id,
                "type": "model",
                "domain": domain,
                "name": node.name,
                "table": table_name,
                "file": rel(path, root),
                "mixins": sorted(mixins),
                "org_scoped": "OrgScopedMixin" in mixins,
                "soft_delete": "SoftDeleteMixin" in mixins,
                "fk_columns": fk_columns,
            }
        )
        for fk in fk_columns:
            ref_table = fk["ref"].split(".")[0]
            fk_edges.append(
                {
                    "type": "fk",
                    "from": model_id,
                    "to_table": ref_table,
                    "column": fk["column"],
                }
            )

    return models, fk_edges


def find_permission(func_node: ast.AST) -> str | None:
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call) and callable_name(sub.func) == "require_permission":
            if sub.args and isinstance(sub.args[0], ast.Constant):
                return sub.args[0].value
    return None


def guess_request_model(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    args = func_node.args
    positional = args.posonlyargs + args.args
    defaults = list(args.defaults)
    pad = len(positional) - len(defaults)
    default_by_arg: dict[str, ast.expr | None] = {}
    for i, arg in enumerate(positional):
        default_by_arg[arg.arg] = defaults[i - pad] if i >= pad else None
    for kwarg, default in zip(args.kwonlyargs, args.kw_defaults):
        default_by_arg[kwarg.arg] = default

    for arg in positional + args.kwonlyargs:
        if arg.arg in NON_SCHEMA_ARG_NAMES or arg.arg.startswith("_"):
            continue
        default = default_by_arg.get(arg.arg)
        if isinstance(default, ast.Call) and callable_name(default.func) == "Depends":
            continue
        ann = unparse(arg.annotation)
        if not ann or ann in NON_SCHEMA_ANNOTATIONS:
            continue
        if ann.startswith(("Session", "list[UploadFile]", "UploadFile")):
            continue
        return ann
    return None


def extract_routes(path: Path, root: Path, domain: str, warnings: list[str]) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        warnings.append(f"routes.py parse error in {path}: {exc}")
        return []

    router_prefix: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and callable_name(node.value.func) == "APIRouter"
        ):
            prefix = ""
            for kw in node.value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = kw.value.value
            for target in node.targets:
                if isinstance(target, ast.Name):
                    router_prefix[target.id] = prefix

    endpoints: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            router_var = deco.func.value.id if isinstance(deco.func.value, ast.Name) else None
            method = deco.func.attr
            if router_var not in router_prefix or method not in HTTP_VERBS:
                continue
            route_suffix = ""
            if deco.args and isinstance(deco.args[0], ast.Constant):
                route_suffix = deco.args[0].value
            response_model = None
            for kw in deco.keywords:
                if kw.arg == "response_model":
                    response_model = unparse(kw.value)
            prefix = router_prefix[router_var].rstrip("/")
            suffix = "/" + route_suffix.lstrip("/") if route_suffix else ""
            full_path = (prefix + suffix) or "/"
            endpoint_id = f"endpoint:{domain}:{method.upper()}:{full_path}"
            endpoints.append(
                {
                    "id": endpoint_id,
                    "type": "endpoint",
                    "domain": domain,
                    "method": method.upper(),
                    "path": full_path,
                    "permission": find_permission(node),
                    "file": rel(path, root),
                    "response_model": response_model,
                    "request_model": guess_request_model(node),
                }
            )
    return endpoints


def extract_schemas(path: Path, root: Path, domain: str, warnings: list[str]) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        warnings.append(f"schemas.py parse error in {path}: {exc}")
        return []
    schemas = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            schemas.append(
                {
                    "id": f"schema:{domain}:{node.name}",
                    "type": "schema",
                    "domain": domain,
                    "name": node.name,
                    "file": rel(path, root),
                }
            )
    return schemas


def extract_celery_tasks(path: Path, root: Path, warnings: list[str]) -> list[dict]:
    if not path.exists():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    except SyntaxError as exc:
        warnings.append(f"tasks.py parse error in {path}: {exc}")
        return []
    tasks = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if (
                isinstance(deco, ast.Call)
                and isinstance(deco.func, ast.Attribute)
                and deco.func.attr == "task"
                and isinstance(deco.func.value, ast.Name)
                and deco.func.value.id == "celery_app"
            ):
                retries = any(kw.arg == "autoretry_for" for kw in deco.keywords)
                tasks.append(
                    {
                        "id": f"celery_task:{node.name}",
                        "type": "celery_task",
                        "name": node.name,
                        "file": rel(path, root),
                        "autoretry": retries,
                    }
                )
    return tasks


def extract_integrations(integrations_dir: Path, root: Path) -> list[dict]:
    if not integrations_dir.exists():
        return []
    integrations = []
    for file in sorted(integrations_dir.glob("*.py")):
        if file.name.startswith("_") or file.name == "__init__.py":
            continue
        text = file.read_text(encoding="utf-8-sig")
        flags = sorted(set(re.findall(r"settings\.mock_(\w+)", text)))
        if not flags:
            continue
        integrations.append(
            {
                "id": f"integration:{file.stem}",
                "type": "integration",
                "name": file.stem,
                "mock_flags": [f"MOCK_{f.upper()}" for f in flags],
                "file": rel(file, root),
            }
        )
    return integrations


def extract_migrations(versions_dir: Path, root: Path, warnings: list[str]) -> tuple[list[dict], list[dict]]:
    if not versions_dir.exists():
        return [], []
    migrations = []
    down_edges = []
    for file in sorted(versions_dir.glob("*.py")):
        if file.name == "__init__.py":
            continue
        try:
            tree = ast.parse(file.read_text(encoding="utf-8-sig"), filename=str(file))
        except SyntaxError as exc:
            warnings.append(f"migration parse error in {file}: {exc}")
            continue
        revision = None
        down_revision: list[str] = []
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            name = node.targets[0].id
            if name == "revision" and isinstance(node.value, ast.Constant):
                revision = node.value.value
            elif name == "down_revision":
                if isinstance(node.value, ast.Constant):
                    if node.value.value is not None:
                        down_revision = [node.value.value]
                elif isinstance(node.value, ast.Tuple):
                    down_revision = [
                        elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and elt.value is not None
                    ]
        if revision is None:
            warnings.append(f"could not find `revision =` in {file}")
            continue
        if len(revision) > 32:
            warnings.append(f"revision id '{revision}' exceeds 32 chars ({file})")
        mig_id = f"migration:{revision}"
        migrations.append(
            {
                "id": mig_id,
                "type": "migration",
                "revision": revision,
                "down_revision": down_revision,
                "file": rel(file, root),
            }
        )
        for parent in down_revision:
            down_edges.append({"type": "migration_down", "from": mig_id, "to": f"migration:{parent}"})
    return migrations, down_edges


# --------------------------------------------------------------------------
# Frontend: depth-aware lexer
# --------------------------------------------------------------------------


def compute_depth_and_code_mask(text: str) -> tuple[list[int], list[int], list[bool]]:
    """For every character index: the brace depth and the paren depth *after*
    processing that character, and whether it's real code (not inside a
    string/comment). Template-literal `${...}` expressions are treated as
    real code (their braces/parens genuinely nest), everything else inside
    `` ` ``/"/'/comments is not.

    Paren depth matters because a parameter type annotation like
    `(id: string) => ...` contains an `identifier:` that is NOT a top-level
    object-literal key -- it's inside parens, at the same BRACE depth as the
    enclosing object body (parens don't open a brace). Top-level keys must be
    filtered on brace depth AND paren depth == 0.
    """
    n = len(text)
    depth_at = [0] * n
    paren_at = [0] * n
    in_code = [True] * n
    depth = 0
    paren = 0
    mode = "code"  # code | line_comment | block_comment | dq | sq | template
    template_expr_depths: list[int] = []  # brace depths at which a `${` was opened
    i = 0
    while i < n:
        c = text[i]
        if mode == "code":
            if c == "/" and i + 1 < n and text[i + 1] == "/":
                mode = "line_comment"
                in_code[i] = False
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                mode = "block_comment"
                in_code[i] = False
            elif c == '"':
                mode = "dq"
                in_code[i] = False
            elif c == "'":
                mode = "sq"
                in_code[i] = False
            elif c == "`":
                mode = "template"
                in_code[i] = False
            else:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if template_expr_depths and template_expr_depths[-1] == depth - 1:
                        template_expr_depths.pop()
                        depth -= 1
                        depth_at[i] = depth
                        paren_at[i] = paren
                        i += 1
                        mode = "template"
                        continue
                    depth -= 1
                elif c == "(":
                    paren += 1
                elif c == ")":
                    paren -= 1
                in_code[i] = True
            depth_at[i] = depth
            paren_at[i] = paren
        elif mode == "line_comment":
            in_code[i] = False
            depth_at[i] = depth
            paren_at[i] = paren
            if c == "\n":
                mode = "code"
        elif mode == "block_comment":
            in_code[i] = False
            depth_at[i] = depth
            paren_at[i] = paren
            if c == "*" and i + 1 < n and text[i + 1] == "/":
                depth_at[i + 1] = depth
                paren_at[i + 1] = paren
                in_code[i + 1] = False
                i += 2
                mode = "code"
                continue
        elif mode in ("dq", "sq"):
            in_code[i] = False
            depth_at[i] = depth
            paren_at[i] = paren
            quote = '"' if mode == "dq" else "'"
            if c == "\\" and i + 1 < n:
                depth_at[i + 1] = depth
                paren_at[i + 1] = paren
                in_code[i + 1] = False
                i += 2
                continue
            if c == quote:
                mode = "code"
        elif mode == "template":
            in_code[i] = False
            depth_at[i] = depth
            paren_at[i] = paren
            if c == "\\" and i + 1 < n:
                depth_at[i + 1] = depth
                paren_at[i + 1] = paren
                in_code[i + 1] = False
                i += 2
                continue
            if c == "`":
                mode = "code"
            elif c == "$" and i + 1 < n and text[i + 1] == "{":
                template_expr_depths.append(depth)
                depth += 1
                depth_at[i] = depth
                paren_at[i] = paren
                in_code[i] = True
                i += 1
                depth_at[i] = depth
                paren_at[i] = paren
                in_code[i] = True
                mode = "code"
        i += 1
    return depth_at, paren_at, in_code


def find_matching_close(depth_at: list[int], in_code: list[bool], text: str, open_idx: int) -> int:
    target_depth = depth_at[open_idx] - 1
    for j in range(open_idx + 1, len(text)):
        if in_code[j] and text[j] == "}" and depth_at[j] == target_depth:
            return j
    return len(text)


STRING_LITERAL_RE = re.compile(r"`([^`]*)`|\"([^\"]*)\"|'([^']*)'")
API_CALL_NAME_RE = re.compile(r"\b(apiFetch|apiDownload)\b")


def skip_balanced_angle_brackets(text: str, start: int) -> tuple[int, str | None]:
    """text[start] must be '<'. Returns (index just past the matching '>',
    inner text) or (start, None) if unbalanced. Handles nested generics like
    `Record<string, Foo>` inside `apiFetch<Record<string, Foo>>`."""
    if start >= len(text) or text[start] != "<":
        return start, None
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "<":
            depth += 1
        elif text[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1, text[start + 1 : i]
        i += 1
    return start, None


def find_api_call(chunk: str) -> tuple[str, str | None, int] | None:
    """Finds an apiFetch/apiDownload call in `chunk`, correctly skipping
    nested generic type args (e.g. `Record<string, Foo>`). Returns
    (call_name, response_type_or_None, index_just_past_the_opening_paren),
    or None."""
    for m in API_CALL_NAME_RE.finditer(chunk):
        pos = m.end()
        while pos < len(chunk) and chunk[pos].isspace():
            pos += 1
        response_type = None
        if pos < len(chunk) and chunk[pos] == "<":
            new_pos, response_type = skip_balanced_angle_brackets(chunk, pos)
            if response_type is None:
                continue
            pos = new_pos
        while pos < len(chunk) and chunk[pos].isspace():
            pos += 1
        if pos < len(chunk) and chunk[pos] == "(":
            return m.group(1), response_type, pos + 1
    return None


def extract_endpoint_groups(path: Path, root: Path, warnings: list[str]) -> tuple[list[dict], list[dict]]:
    """Returns (group_nodes, fn_nodes)."""
    text = path.read_text(encoding="utf-8-sig")
    depth_at, paren_at, in_code = compute_depth_and_code_mask(text)
    rel_file = rel(path, root)

    groups: list[dict] = []
    fns: list[dict] = []

    for gmatch in re.finditer(r"export const (\w+)\s*=\s*\{", text):
        group_name = gmatch.group(1)
        open_idx = gmatch.end() - 1
        close_idx = find_matching_close(depth_at, in_code, text, open_idx)
        body_depth = depth_at[open_idx]

        body_paren = paren_at[open_idx]
        key_positions: list[tuple[int, int, str]] = []
        for kmatch in re.finditer(r"([A-Za-z_$][\w$]*)\s*:", text[open_idx + 1 : close_idx]):
            abs_start = open_idx + 1 + kmatch.start()
            if in_code[abs_start] and depth_at[abs_start] == body_depth and paren_at[abs_start] == body_paren:
                key_positions.append((abs_start, open_idx + 1 + kmatch.end(), kmatch.group(1)))

        method_count = len(key_positions)
        groups.append(
            {
                "id": f"endpoint_client_group:{group_name}",
                "type": "endpoint_client_group",
                "name": group_name,
                "file": rel_file,
                "method_count": method_count,
            }
        )

        for idx, (key_start, value_start, method_name) in enumerate(key_positions):
            chunk_end = key_positions[idx + 1][0] if idx + 1 < len(key_positions) else close_idx
            chunk = text[value_start:chunk_end]

            call_found = find_api_call(chunk)
            if not call_found:
                warnings.append(f"endpoints.ts: {group_name}.{method_name} has no apiFetch/apiDownload call")
                continue
            call_name, response_type, call_body_start = call_found
            rest = chunk[call_body_start:]
            path_match = STRING_LITERAL_RE.search(rest)
            if not path_match:
                warnings.append(f"endpoints.ts: {group_name}.{method_name} call has no string path literal")
                continue
            raw_path = next(g for g in path_match.groups() if g is not None)
            normalized_path = re.sub(r"\$\{[^}]*\}", "{param}", raw_path)

            method_kw = re.search(r"method:\s*[\"'](\w+)[\"']", chunk)
            is_download = call_name == "apiDownload"
            http_method = (method_kw.group(1) if method_kw else "GET").upper()

            fns.append(
                {
                    "id": f"endpoint_client_fn:{group_name}.{method_name}",
                    "type": "endpoint_client_fn",
                    "group": group_name,
                    "method_name": method_name,
                    "http_method": http_method,
                    "path_template": raw_path,
                    "path_normalized": normalized_path,
                    "response_type": response_type,
                    "download": is_download,
                }
            )

    return groups, fns


def extract_ts_interfaces(path: Path, root: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    rel_file = rel(path, root)
    interfaces = []
    for m in re.finditer(r"^export (?:interface|type) (\w+)", text, re.MULTILINE):
        interfaces.append(
            {
                "id": f"ts_interface:{m.group(1)}",
                "type": "ts_interface",
                "name": m.group(1),
                "file": rel_file,
            }
        )
    return interfaces


def extract_ui_primitives(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    names = sorted(set(re.findall(r"export (?:function|const|interface) (\w+)", text)))
    return [{"id": f"ui_primitive:{n}", "type": "ui_primitive", "name": n} for n in names]


def extract_pages(app_dir: Path, root: Path) -> tuple[list[dict], list[str]]:
    """Returns (page_nodes, frontend_domain_names)."""
    if not app_dir.exists():
        return [], []
    pages = []
    domains: set[str] = set()
    for page in sorted(app_dir.rglob("page.tsx")):
        page_rel = page.relative_to(app_dir)
        parts = page_rel.parts[:-1]  # drop "page.tsx"
        route_parts = [p.replace("[", "{").replace("]", "}") for p in parts]
        route = "/" + "/".join(route_parts) if route_parts else "/"
        feature_domain = parts[0] if parts else "_root"
        domains.add(feature_domain)
        components = sorted(f.name for f in page.parent.glob("_*.tsx"))
        pages.append(
            {
                "id": f"page:{route}",
                "type": "page",
                "route": route,
                "domain": feature_domain,
                "file": rel(page, root),
                "components": components,
            }
        )
    return pages, sorted(domains)


# --------------------------------------------------------------------------
# Domain pairing
# --------------------------------------------------------------------------


def pair_domains(
    backend_domain_names: list[str],
    frontend_group_names: list[str],
    aliases: dict[str, str],
    warnings: list[str],
) -> list[dict]:
    edges = []
    frontend_by_norm: dict[str, str] = {}
    for g in frontend_group_names:
        frontend_by_norm.setdefault(norm_key(g), g)

    matched_frontend: set[str] = set()
    for backend in backend_domain_names:
        nb = norm_key(backend)
        confidence = None
        target = None

        if backend in aliases:
            target = aliases[backend]
            confidence = "alias"
        elif nb in frontend_by_norm:
            target = frontend_by_norm[nb]
            confidence = "exact"
        else:
            for ng, group in frontend_by_norm.items():
                if group in matched_frontend:
                    continue
                if nb and ng and (nb in ng or ng in nb):
                    target = group
                    confidence = "heuristic"
                    break

        if target:
            matched_frontend.add(target)
            edges.append(
                {
                    "type": "frontend_counterpart",
                    "from": f"domain:backend:{backend}",
                    "to": f"endpoint_client_group:{target}",
                    "confidence": confidence,
                }
            )
        else:
            warnings.append(
                f"no frontend Api group matched for backend domain '{backend}' "
                f"(add an entry to specs/_graph/domain-aliases.json to resolve)"
            )

    for group in frontend_group_names:
        if group not in matched_frontend:
            warnings.append(
                f"frontend Api group '{group}' did not match any backend domain "
                f"(add an entry to specs/_graph/domain-aliases.json to resolve)"
            )
    return edges


def match_client_calls_to_endpoints(fns: list[dict], endpoints: list[dict], warnings: list[str]) -> list[dict]:
    def wildcard(p: str) -> str:
        return re.sub(r"\{[^}]*\}", "{*}", p)

    lookup: dict[tuple[str, str], str] = {}
    for ep in endpoints:
        key = (ep["method"], wildcard(ep["path"]))
        lookup.setdefault(key, ep["id"])

    edges = []
    unmatched = 0
    for fn in fns:
        key = (fn["http_method"], wildcard(fn["path_normalized"]))
        target = lookup.get(key)
        edges.append(
            {
                "type": "calls",
                "from": fn["id"],
                "to": target,
                "matched": target is not None,
            }
        )
        if target is None:
            unmatched += 1
    if unmatched:
        warnings.append(f"{unmatched} frontend client call(s) did not match any backend endpoint by method+path")
    return edges


# --------------------------------------------------------------------------
# Markdown generation
# --------------------------------------------------------------------------


def render_index_md(graph: dict) -> str:
    meta = graph["metadata"]
    domains_backend = [n for n in graph["nodes"] if n["type"] == "domain_backend"]
    domains_frontend = [n for n in graph["nodes"] if n["type"] == "domain_frontend"]
    counterpart_by_backend = {
        e["from"]: e for e in graph["edges"] if e["type"] == "frontend_counterpart"
    }

    lines = [
        "<!-- GENERATED by scripts/build_repo_graph.py -- do not hand-edit. -->",
        "# Repo knowledge graph -- index",
        "",
        f"Generated: {meta['generated_at']} (git HEAD: {meta['git_head'] or 'unknown'})",
        "",
        "## Cross-cutting conventions",
        "",
        "- SQLAlchemy model mixins (`backend/app/core/database.py`): "
        + ", ".join(sorted(MIXIN_NAMES)) + ".",
        "- Table names auto-derive from the class name via `TableNameMixin` "
        "(CamelCase -> snake_case, singular) unless a model sets `__tablename__` explicitly.",
        "- Permission strings follow `\"<resource>:<action>\"`, "
        "gated via `Depends(require_permission(...))`. Not every route uses it "
        "(some domains do manual auth) -- an endpoint with `permission: null` below is not "
        "necessarily a bug, verify against `.claude/rules/constitution.md`.",
        "- External integrations are gated by `settings.mock_<name>` "
        "(env var `MOCK_<NAME>`), see `backend/app/integrations/`.",
        "- Alembic revision IDs are `00NN_short_slug`, <= 32 chars, single chain "
        "(merges use a tuple `down_revision`).",
        "",
        "## Backend domains x frontend counterpart",
        "",
        "| Backend domain | Files | Frontend Api group | Confidence |",
        "|---|---|---|---|",
    ]
    for d in domains_backend:
        edge = counterpart_by_backend.get(d["id"])
        counterpart = edge["to"].split(":", 1)[1] if edge else "*(none)*"
        confidence = edge["confidence"] if edge else "unmatched"
        lines.append(
            f"| {d['name']} | {', '.join(d['files']) or '(none)'} | {counterpart} | {confidence} |"
        )
    lines += [
        "",
        "## Frontend feature folders",
        "",
        ", ".join(d["name"] for d in domains_frontend),
        "",
        "## Counts",
        "",
    ]
    for k, v in meta["counts"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Warnings", ""]
    if meta["warnings"]:
        lines += [f"- {w}" for w in meta["warnings"]]
    else:
        lines.append("(none)")
    lines += [
        "",
        "## Out of scope (v1)",
        "",
        "- Endpoint -> model data-flow tracing through service.py internals "
        "(would need real call-graph analysis).",
        "- A live/dynamic graph, an external graph DB, or a rendered visual diagram.",
        "",
        "Per-domain detail: `specs/_graph/domains/<domain>.md`.",
    ]
    return "\n".join(lines) + "\n"


def render_domain_md(domain_name: str, graph: dict) -> str:
    nodes_by_type: dict[str, list[dict]] = {}
    for n in graph["nodes"]:
        nodes_by_type.setdefault(n["type"], []).append(n)

    models = [m for m in nodes_by_type.get("model", []) if m["domain"] == domain_name]
    endpoints = [e for e in nodes_by_type.get("endpoint", []) if e["domain"] == domain_name]
    schemas = [s for s in nodes_by_type.get("schema", []) if s["domain"] == domain_name]
    domain_node = next(
        (d for d in nodes_by_type.get("domain_backend", []) if d["name"] == domain_name), None
    )
    migrations_touching = [
        m
        for m in nodes_by_type.get("migration", [])
        if any(model["table"] in m["file"] for model in models) or domain_name in m["file"]
    ]
    counterpart_edge = next(
        (
            e
            for e in graph["edges"]
            if e["type"] == "frontend_counterpart" and e["from"] == f"domain:backend:{domain_name}"
        ),
        None,
    )
    fns_by_group: dict[str, list[dict]] = {}
    for fn in nodes_by_type.get("endpoint_client_fn", []):
        fns_by_group.setdefault(fn["group"], []).append(fn)

    lines = [
        "<!-- GENERATED by scripts/build_repo_graph.py -- do not hand-edit. -->",
        f"# Domain: {domain_name}",
        "",
        f"Files present: {', '.join(domain_node['files']) if domain_node else '(unknown)'}",
        f"Owner agents: {', '.join(domain_node['owner_agents']) if domain_node else '(unknown)'}",
        "",
        "## Models",
        "",
    ]
    if models:
        for m in models:
            fk_desc = ", ".join(f"{c['column']}->{c['ref']}" for c in m["fk_columns"]) or "(none)"
            lines.append(
                f"- `{m['name']}` (table `{m['table']}`, org_scoped={m['org_scoped']}, "
                f"soft_delete={m['soft_delete']}) -- FKs: {fk_desc}"
            )
    else:
        lines.append("(no models.py, or no model classes found)")

    lines += ["", "## Endpoints", ""]
    if endpoints:
        for e in endpoints:
            perm = e["permission"] or "*(none -- verify manual auth)*"
            lines.append(
                f"- `{e['method']} {e['path']}` -- permission: `{perm}` -- "
                f"request: {e['request_model'] or '?'} -- response: {e['response_model'] or '?'}"
            )
    else:
        lines.append("(no routes.py, or no routes found)")

    lines += ["", "## Schemas", ""]
    lines.append(", ".join(s["name"] for s in schemas) if schemas else "(no schemas.py)")

    lines += ["", "## Migrations touching this domain (heuristic: table/domain name in filename)", ""]
    if migrations_touching:
        lines += [f"- `{m['revision']}` ({m['file']})" for m in migrations_touching]
    else:
        lines.append("(none matched -- check specs/_graph/repo-graph.json for the full migration chain)")

    lines += ["", "## Frontend counterpart", ""]
    if counterpart_edge:
        group = counterpart_edge["to"].split(":", 1)[1]
        lines.append(f"Api group: `{group}` (confidence: {counterpart_edge['confidence']})")
        for fn in sorted(fns_by_group.get(group, []), key=lambda f: f["method_name"]):
            lines.append(f"- `{group}.{fn['method_name']}()` -> `{fn['http_method']} {fn['path_template']}`")
    else:
        lines.append("*(no frontend Api group matched -- see warnings in index.md)*")

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def git_head(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return None


def build_graph(root: Path) -> dict:
    warnings: list[str] = []
    nodes: list[dict] = []
    edges: list[dict] = []

    backend_app = root / "backend" / "app"
    backend_domains = find_backend_domains(backend_app)
    backend_domain_names = [d.name for d in backend_domains]

    all_models: list[dict] = []
    all_fk_table_edges: list[dict] = []
    all_endpoints: list[dict] = []

    for domain_dir in backend_domains:
        domain = domain_dir.name
        files_present = [
            f for f in ("models.py", "routes.py", "service.py", "schemas.py", "access.py")
            if (domain_dir / f).exists()
        ]
        owner_agents = sorted({FILE_ROLE_OWNER[f] for f in files_present}) or ["(none)"]
        nodes.append(
            {
                "id": f"domain:backend:{domain}",
                "type": "domain_backend",
                "name": domain,
                "path": rel(domain_dir, root),
                "files": files_present,
                "owner_agents": owner_agents,
            }
        )

        if "models.py" in files_present:
            models, fk_edges = extract_models(domain_dir / "models.py", root, domain, warnings)
            all_models.extend(models)
            all_fk_table_edges.extend(fk_edges)

        if "routes.py" in files_present:
            all_endpoints.extend(extract_routes(domain_dir / "routes.py", root, domain, warnings))

        if "schemas.py" in files_present:
            nodes.extend(extract_schemas(domain_dir / "schemas.py", root, domain, warnings))

    nodes.extend(all_models)
    nodes.extend(all_endpoints)
    for m in all_models:
        edges.append({"type": "contains", "from": f"domain:backend:{m['domain']}", "to": m["id"]})
    for e in all_endpoints:
        edges.append({"type": "contains", "from": f"domain:backend:{e['domain']}", "to": e["id"]})

    table_to_model_id = {m["table"]: m["id"] for m in all_models}
    for fk in all_fk_table_edges:
        target = table_to_model_id.get(fk["to_table"])
        edges.append(
            {
                "type": "fk",
                "from": fk["from"],
                "to": target or f"table:{fk['to_table']}",
                "column": fk["column"],
                "resolved": target is not None,
            }
        )

    celery_tasks = extract_celery_tasks(backend_app / "jobs" / "tasks.py", root, warnings)
    nodes.extend(celery_tasks)
    for t in celery_tasks:
        edges.append({"type": "contains", "from": "domain:backend:jobs", "to": t["id"]})

    integrations = extract_integrations(backend_app / "integrations", root)
    nodes.extend(integrations)
    for i in integrations:
        edges.append({"type": "contains", "from": "domain:backend:integrations", "to": i["id"]})

    migrations, down_edges = extract_migrations(root / "backend" / "alembic" / "versions", root, warnings)
    nodes.extend(migrations)
    edges.extend(down_edges)

    frontend_src = root / "frontend" / "src"
    endpoints_ts = frontend_src / "lib" / "endpoints.ts"
    types_ts = frontend_src / "lib" / "types.ts"
    ui_tsx = frontend_src / "components" / "ui.tsx"
    app_dir = frontend_src / "app" / "(app)"

    pages, frontend_domain_names = extract_pages(app_dir, root)
    nodes.extend(pages)
    for p in pages:
        edges.append({"type": "contains", "from": f"domain:frontend:{p['domain']}", "to": p["id"]})
    for name in frontend_domain_names:
        nodes.append(
            {
                "id": f"domain:frontend:{name}",
                "type": "domain_frontend",
                "name": name,
                "path": rel(app_dir / name, root),
            }
        )

    groups: list[dict] = []
    fns: list[dict] = []
    if endpoints_ts.exists():
        groups, fns = extract_endpoint_groups(endpoints_ts, root, warnings)
    nodes.extend(groups)
    nodes.extend(fns)

    ts_interfaces = extract_ts_interfaces(types_ts, root) if types_ts.exists() else []
    nodes.extend(ts_interfaces)

    ui_primitives = extract_ui_primitives(ui_tsx)
    nodes.extend(ui_primitives)

    edges.extend(match_client_calls_to_endpoints(fns, all_endpoints, warnings))

    aliases: dict[str, str] = {}
    aliases_file = root / "specs" / "_graph" / "domain-aliases.json"
    if aliases_file.exists():
        aliases = json.loads(aliases_file.read_text(encoding="utf-8-sig"))
    frontend_group_names = [g["name"] for g in groups]
    edges.extend(pair_domains(backend_domain_names, frontend_group_names, aliases, warnings))

    nodes.sort(key=lambda n: (n["type"], n["id"]))
    edges.sort(key=lambda e: (e["type"], e["from"], str(e.get("to"))))
    warnings.sort()

    counts = {}
    for n in nodes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    counts["edges_total"] = len(edges)

    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_head": git_head(root),
            "counts": counts,
            "warnings": warnings,
        },
    }


def write_outputs(root: Path, graph: dict) -> None:
    graph_dir = root / "specs" / "_graph"
    domains_dir = graph_dir / "domains"
    domains_dir.mkdir(parents=True, exist_ok=True)

    graph_json = json.dumps(graph, indent=2, sort_keys=True) + "\n"
    (graph_dir / "repo-graph.json").write_text(graph_json, encoding="utf-8")

    (graph_dir / "index.md").write_text(render_index_md(graph), encoding="utf-8")

    backend_domain_names = sorted(
        n["name"] for n in graph["nodes"] if n["type"] == "domain_backend"
    )
    existing = {f.name for f in domains_dir.glob("*.md")}
    wanted = set()
    for domain in backend_domain_names:
        (domains_dir / f"{domain}.md").write_text(render_domain_md(domain, graph), encoding="utf-8")
        wanted.add(f"{domain}.md")
    for stale in existing - wanted:
        (domains_dir / stale).unlink()

    aliases_file = graph_dir / "domain-aliases.json"
    if not aliases_file.exists():
        aliases_file.write_text("{}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    graph = build_graph(args.root.resolve())
    write_outputs(args.root.resolve(), graph)

    meta = graph["metadata"]
    print(f"Generated specs/_graph/ (HEAD {meta['git_head'] or 'unknown'})")
    for k, v in meta["counts"].items():
        print(f"  {k}: {v}")
    if meta["warnings"]:
        print(f"  warnings: {len(meta['warnings'])}")
        for w in meta["warnings"]:
            print(f"    - {w}")
    else:
        print("  warnings: 0")


if __name__ == "__main__":
    main()
