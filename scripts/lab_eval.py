"""Dependency-free learner/CI client. Never imports or executes agent code."""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

EXTENSIONS = {
    ".py",
    ".ts",
    ".js",
    ".mjs",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
}
_oidc_cache = None


def language_for(files):
    """Language is declared by agent.json, otherwise unambiguous entry files."""
    names = {item["path"] for item in files}
    manifest = next((item for item in files if item["path"] == "agent.json"), None)
    if manifest:
        config = json.loads(manifest["content"])
        language = config.get("language")
        if config.get("protocol") != "hyper-lab-v1" or language not in {
            "python",
            "typescript",
        }:
            raise ValueError(
                "agent.json must declare protocol hyper-lab-v1 and language python or typescript"
            )
    else:
        languages = [
            language
            for language, suffix in (("python", "py"), ("typescript", "ts"))
            if {f"agent.{suffix}", f"tools.{suffix}"} <= names
        ]
        if len(languages) != 1:
            raise ValueError(
                "Supply agent.py/tools.py or agent.ts/tools.ts; use agent.json to disambiguate"
            )
        language = languages[0]
    suffix = "py" if language == "python" else "ts"
    if not {f"agent.{suffix}", f"tools.{suffix}"} <= names:
        raise ValueError(f"agent.{suffix} and tools.{suffix} are required")
    return language


def github_identity(binding):
    """Obtain a fresh short-lived token; never print it or save it to a file."""
    global _oidc_cache
    binding = str(uuid.UUID(binding))
    if _oidc_cache and _oidc_cache[0] == binding and _oidc_cache[1] > time.time() + 45:
        return _oidc_cache[2]
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    secret = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(".actions.githubusercontent.com")
        or parsed.username
        or parsed.password
        or not secret
    ):
        raise ValueError("Run the generated workflow on GitHub with id-token: write")
    request = Request(
        url
        + ("&" if parsed.query else "?")
        + "audience="
        + quote("hyper-lab:" + binding, safe=""),
        headers={"Authorization": "Bearer " + secret},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            token = json.loads(response.read(32000))["value"]
        # Cache timing only; signature verification happens on the server.
        payload = token.split(".")[1]
        expiry = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )["exp"]
        _oidc_cache = (binding, expiry, token)
        return token
    except Exception:
        raise RuntimeError(
            "GitHub identity request failed; verify id-token permission and retry the workflow"
        ) from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Evaluation API redirects are not accepted")


def collect(root):
    directory = root / "agent"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("A real agent/ directory is required")
    files = []
    for path in sorted(directory.rglob("*")):
        parts = path.relative_to(directory).parts
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed in agent/")
        if any(
            p.startswith(".") or p in {"__pycache__", "node_modules", "venv"}
            for p in parts
        ):
            continue
        if path.is_file() and path.suffix in EXTENSIONS:
            if path.stat().st_size > 256 * 1024:
                raise ValueError("Source file exceeds 256 KiB")
            if path.stem.lower() in {"secrets", "credentials", "id_rsa", "id_ed25519"}:
                raise ValueError("Remove credential files from agent/")
            files.append(
                {"path": "/".join(parts), "content": path.read_text(encoding="utf-8")}
            )
            if len(files) > 128:
                raise ValueError("At most 128 source files are accepted")
    language_for(files)
    return files


def api(method, path, payload=None, key=None):
    base = os.environ.get("HYPER_LAB_URL", "").rstrip("/")
    parsed = urlparse(base)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/", "/lab/api/enterprise-ai"}
        or not (
            parsed.scheme == "https"
            or (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "localhost"}
            )
        )
    ):
        raise ValueError(
            "Set HYPER_LAB_URL to the instructor's HTTPS origin or Enterprise AI endpoint (HTTP only for localhost)"
        )
    binding = os.environ.get("HYPER_LAB_BINDING", "")
    token = (
        github_identity(binding) if binding else os.environ.get("HYPER_LAB_TOKEN", "")
    )
    if binding:
        path = path.replace(
            "/v1/evaluations",
            "/v1/github/" + str(uuid.UUID(binding)) + "/evaluations",
            1,
        )
    if not token and os.environ.get("HYPER_LAB_TOKEN_FILE"):
        token = Path(os.environ["HYPER_LAB_TOKEN_FILE"]).read_text().strip()
    if not token:
        raise ValueError(
            "Set HYPER_LAB_TOKEN_FILE or HYPER_LAB_TOKEN; never commit this credential"
        )
    if any(not 33 <= ord(char) <= 126 for char in token):
        # urllib may include a malformed header's value in its exception text.
        # Reject it here so that diagnostics never echo a credential.
        raise ValueError("Evaluation credential must be a single ASCII token")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    data = None if payload is None else json.dumps(payload, ensure_ascii=True).encode()
    if data and len(data) > 2 * 1024 * 1024:
        raise ValueError("Submission exceeds 2 MiB")
    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except HTTPError as error:
        raise RuntimeError(f"Evaluation API returned HTTP {error.code}") from None


def save_report(result, directory):
    """Retain the receipt even when a later polling request fails or times out."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(
        json.dumps(result, ensure_ascii=True, indent=2)
    )
    markdown = result.get(
        "report_md", f"Job `{result['job_id']}` is {result['status']}\n"
    )
    (directory / "report.md").write_text(markdown)
    return markdown


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["submit", "result"])
    parser.add_argument("--task", choices=["t1", "t2", "p1"], default="t1")
    parser.add_argument("--language", choices=["python", "typescript"])
    parser.add_argument("--domain", choices=["airline_plus", "retail_plus"])
    parser.add_argument(
        "--cases", help="Comma-separated t2 case IDs; omitted means the entire scenario"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--job-id")
    parser.add_argument("--commit")
    parser.add_argument("--source", choices=["manual", "github"], default="manual")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=int, default=21000)
    parser.add_argument("--output", type=Path, default=Path("evaluation-results"))
    args = parser.parse_args()
    if args.command == "submit":
        files = collect(args.project.resolve())
        language = language_for(files)
        manifest = next(
            (
                json.loads(item["content"])
                for item in files
                if item["path"] == "agent.json"
            ),
            {},
        )
        domain = args.domain or manifest.get("domain", "airline_plus")
        if domain not in {"airline_plus", "retail_plus"}:
            raise ValueError("Unsupported agent.json domain")
        if args.domain and manifest.get("domain") not in {None, args.domain}:
            raise ValueError(
                "Agent scenario differs from workflow; reconnect or correct agent.json"
            )
        if args.language and args.language != language:
            raise ValueError(
                "Agent language differs from the bound workflow; reconnect or correct agent.json"
            )
        payload = {
            "task": args.task,
            "source": args.source,
            "commit_sha": args.commit,
            "files": files,
            "language": language,
            "domain": domain,
            "case_ids": args.cases.split(",") if args.cases else None,
        }
        result = api("POST", "/v1/evaluations", payload, uuid.uuid4().hex)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "job_id",
                        "status",
                        "snapshot_sha256",
                        "environment_version",
                    )
                }
            ),
            flush=True,
        )
    else:
        job_id = str(uuid.UUID(args.job_id))
        result = api("GET", "/v1/evaluations/" + job_id)
    save_report(result, args.output)
    deadline = time.monotonic() + args.timeout
    while args.wait and result["status"] in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Wait timed out. Job {result['job_id']} remains queryable; do not resubmit blindly."
            )
        time.sleep(2)
        result = api("GET", "/v1/evaluations/" + result["job_id"])
        save_report(result, args.output)
    markdown = save_report(result, args.output)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as handle:
            handle.write(markdown)
    print(markdown)
    if result["status"] in {"queued", "running"}:
        return 0
    if result["status"] == "error":
        return 2
    return 0 if result.get("report", {}).get("verdict") == "passed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Evaluation client: {error}", file=sys.stderr)
        sys.exit(2)
