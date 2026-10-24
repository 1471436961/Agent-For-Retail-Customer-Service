"""Offline static audit of the exact agent/ submission boundary. No execution/network."""
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE = {"binding": "3d81f316-16c1-452d-939f-48776870bd23", "branch": "main",
         "domain": "retail_plus", "language": "python", "task": "t2",
         "case_selection_source": "platform_binding"}


def audit(directory):
    files = sorted(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    errors, dependencies, total, hashes = [], set(), 0, {}
    metadata = None
    for path in files:
        rel = path.relative_to(directory).as_posix()
        if any(marker in path.name.lower() for marker in ("enterprise-ai", "secret", "token", "credential", ".env")) or path.suffix not in {".py", ".json"}:
            errors.append(f"unapproved/credential file (not read): {rel}")
            continue
        ancestors = (path, *path.parents)
        if (not path.resolve().is_relative_to(directory.resolve()) or
                any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
                    for p in ancestors if p == directory or directory in p.parents)):
            errors.append(f"linked/escaping file (not read): {rel}")
            continue
        data = path.read_bytes()
        total += len(data)
        hashes[rel] = hashlib.sha256(data).hexdigest()
        if path.suffix not in {".py", ".json"} or any(part.startswith(".") for part in Path(rel).parts):
            errors.append(f"unapproved package file: {rel}")
        if len(data) > 256 * 1024:
            errors.append(f"file too large: {rel}")
        text = data.decode("utf-8")
        if rel == "agent.json":
            metadata = json.loads(text)
        for marker in ("Enterprise-AI.json", "E:\\", "E:/", "sk-", "ghp_", "gho_", "-----BEGIN PRIVATE KEY", "pending.customer@example.test", "developer_pending_9001"):
            if marker in text:
                errors.append(f"credential/local/test marker: {rel}")
        if path.suffix == ".py":
            tree = ast.parse(text, filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for name in names:
                        root = name.split(".")[0]
                        dependencies.add(root)
                        if root not in sys.stdlib_module_names | {"tau2", "pydantic", "support_agent"}:
                            errors.append(f"unprovided import {root}: {rel}")
                        if root in {"subprocess", "socket", "requests", "httpx"} or name == "urllib.request":
                            errors.append(f"external execution/network import: {rel}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec", "open", "__import__"}:
                    errors.append(f"unsafe dynamic execution/file access: {rel}")
    if len(files) > 128 or total > 2 * 1024 * 1024:
        errors.append("package limit exceeded")
    if metadata != {"protocol": "hyper-lab-v1", "language": "python", "domain": "retail_plus"}:
        errors.append("agent metadata differs from binding")
    return {"files": len(files), "bytes": total, "imports": sorted(dependencies), "sha256": hashes,
            "errors": errors, "passed": not errors}


if __name__ == "__main__":
    report = audit(ROOT / "agent")
    report["scope"] = SCOPE
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["passed"] else 1)
