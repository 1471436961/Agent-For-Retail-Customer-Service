"""Developer-only public-requirement index, never imported or shipped in agent/."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path("E:/Agent-For-Retail-Customer-Service-Materials/test-cases-3d81f316-16c1-452d-939f-48776870bd23-zh.md")

# Manually reviewed capability mapping, not runtime routing or expected answers.
# Global protocol/identity/confirmation/type/state/output/scope apply to all cases.
SPECIFIC = {
0:[6,15],1:[6,15],2:[6,14],3:[6,13,16],4:[6,13,16],5:[6,7,14,15],6:[6,7,15],7:[6,7,15],8:[6,7,15],9:[6,7,15],
10:[14,16,19],11:[14,16],12:[12,14,19],13:[12,14],14:[12,14],15:[6,13],16:[9,12,14,16],17:[10],18:[7,14,15],19:[6,9,14,15,16],
20:[6,13,15,16],21:[4,6,7,9,15],22:[7,10,16],23:[6,13,15,16],24:[5,7,12],25:[4,5,14,19],26:[4,5,14],27:[6,14,15],28:[9,12,14,16],29:[5,6,15],
30:[5,12,14,15,16],31:[5,12,14,19],32:[5,12,14,19],33:[9,10,12,14,19],34:[9,10,12,14,19],35:[6,13,14],36:[6,9,11,12,13,19],37:[6,9,11,12,13,19],38:[6,9,11,12,13,19],39:[6,10,19],
40:[5,9,11],41:[6,10,13,16],42:[6,10,13,16],43:[5,10],44:[6,9,13],45:[4,6,9,15],46:[4,9,14],47:[4,9,14],48:[7,14],49:[5,6,15],
50:[8,19],51:[4,14],52:[6,15],53:[14],54:[6,9,12,14,15,16],55:[12,14,16],56:[5,6,13,19],57:[5,7,12,19],58:[6,15],59:[5,9,10,12,16],
60:[6,9,13],61:[6,9,13],62:[5,6,12,19],63:[5,6,12,13,19],64:[6,13,15],65:[6,19],66:[6,12,13,14,19],67:[5,9],68:[5,9],69:[8,12,14],
70:[6,9,15],71:[6,7,10,13,16],72:[6,7,10,13,16],73:[14],74:[6,12,15,16],75:[6,15],76:[5,6,9,12,13,16,19],77:[6,15],78:[6,10,12,13,16],79:[5,6,15],
80:[6,15],81:[12,19],82:[6,14],83:[6,14],84:[6,7,14],85:[6,15],86:[6,10,15,16],87:[10,16],88:[6,12,13],89:[6,9,14,15],
90:[6,9,12,13],91:[6,7,14,15,16],92:[7,14],93:[4,6,15],94:[4,6,15],95:[6,9,15,16],96:[6,10,13,16],97:[6,10,13,16],98:[6,7,12,13,15,16],99:[6,12,13,15,16,19],
100:[6,13,14,15,16],101:[4,6,10,13,16],102:[6,10,13,15,16],103:[5,6,10,13,14,16],104:[5,6,10,13,14,16],105:[6,15],106:[6,15],107:[6,15,16],108:[9,14],109:[6,10,13,16],
110:[6,10,13,16],111:[6,10,13,15,16],112:[6,10,13,15,16],113:[12,16],114:[9,11],115:[9,11],116:[9,12],117:[6,9,15],118:[6,9,15],119:[6,9,13],
120:[6,8,15],121:[4,12],122:[9,14],123:[9,11],124:[9,14],125:[6,8,9,13],126:[3,4],127:[9,12],128:[8,10],129:[11],130:[19],131:[6,9,13,19],132:[19],133:[6,12,19]}


def build():
    text = SOURCE.read_text(encoding="utf-8-sig")
    sections = list(re.finditer(r"(?ms)^## .*? ID (\d+)\r?\n(.*?)(?=^## |\Z)", text))
    assert [int(m[1]) for m in sections] == list(range(134))
    rows = []
    for section in sections:
        identifier = int(section[1])
        capabilities = sorted({1,2,3,4,7,8,17,18,20,21,22, *SPECIFIC[identifier]})
        rows.append({"public_requirement_id": identifier, "source_line": text[:section.start()].count("\n") + 1,
                     "source_section_sha256": hashlib.sha256(section[0].encode()).hexdigest(),
                     "capabilities": [f"CV-{n:02}" for n in capabilities],
                     "offline_evidence": "capability tests, see P7-COVERAGE.md; not this public case execution",
                     "real_model_and_case_status": "not_run"})
    report = {"source": str(SOURCE), "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "scope": "developer requirement mapping only; binding remains t1/case_ids:null",
              "requirements": rows, "count": len(rows)}
    (ROOT / "docs/P7-REQUIREMENTS.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(f"Mapped {build()['count']} public requirements; no model or evaluation executed.")
