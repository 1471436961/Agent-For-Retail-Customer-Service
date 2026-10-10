"""Execute actual offline unittest regression and package audit, then record results."""
import datetime as dt
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ.setdefault("LOGURU_LEVEL", "ERROR")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
spec = importlib.util.spec_from_file_location("submission_audit", ROOT / "scripts/check-submission.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
package = audit.audit(ROOT / "agent")
report = {"stage": "P7", "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
          "command": ".\\.venv\\Scripts\\python.exe scripts/verify-offline.py", "python": sys.executable,
          "tests": {"executed": result.testsRun, "failed": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped), "passed": result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped)},
          "package": package, "scope": audit.SCOPE, "public_requirements_mapped": 134,
          "real_provider_called": False, "public_business_cases_executed": 0,
          "scope_kind": "installation snapshot; not a live platform binding",
          "online_status": "This offline command does not launch or verify online evaluations; inspect actual platform reports separately.",
          "semantic_understanding": "not verified; provider decisions are mocked", "platform_replay": "not verified; all-call replay tested offline",
          "status": "passed" if result.wasSuccessful() and package["passed"] else "failed"}
(ROOT / "docs/P7-VALIDATION.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
sys.exit(0 if report["status"] == "passed" else 1)
