"""Read the exported project domain; never infer a domain from a customer ID."""
import json
from pathlib import Path

DOMAIN = json.loads((Path(__file__).resolve().parents[1] / "agent.json").read_text())["domain"]

# Preference only: the injected gateway remains the authority for allowed model
# IDs, provider routing, credentials and fixed generation parameters.
PREFERRED_MODEL = "kimi-k2.6"
