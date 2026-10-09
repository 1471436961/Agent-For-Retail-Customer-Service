"""Read the exported project domain; never infer a domain from a customer ID."""
import json
from pathlib import Path

DOMAIN = json.loads((Path(__file__).resolve().parents[1] / "agent.json").read_text())["domain"]
