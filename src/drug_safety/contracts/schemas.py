"""Write or check the JSON schemas in contracts/schemas/ (for non-Python consumers and review).

python -m drug_safety.contracts.schemas write   # regenerate after changing models.py
python -m drug_safety.contracts.schemas check   # fail if the files don't match models.py
"""

import json
import sys
from pathlib import Path

from drug_safety.contracts import AssessRequest, AssessResponse, Flag, Patient, Report

SCHEMA_DIR = Path(__file__).parent / "schemas"
MODELS = {
    "patient": Patient,
    "flag": Flag,
    "report": Report,
    "assess_request": AssessRequest,
    "assess_response": AssessResponse,
}


def render(model) -> str:
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def main(argv=None) -> int:
    cmd = (argv or sys.argv[1:] or ["check"])[0]
    SCHEMA_DIR.mkdir(exist_ok=True)
    stale = []
    for name, model in MODELS.items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        if cmd == "write":
            path.write_text(render(model))
        elif not path.exists() or path.read_text() != render(model):
            stale.append(path.name)
    if stale:
        print(
            "out of date:",
            ", ".join(stale),
            "-> run: python -m drug_safety.contracts.schemas write",
        )
        return 1
    print("schemas written" if cmd == "write" else "schemas up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
