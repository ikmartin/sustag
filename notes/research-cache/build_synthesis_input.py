#!/usr/bin/env python3
"""Build synthesis-input.json for the deferred sustag synthesis run.

Combines:
  - PROJECT and RULES template literals from the original workflow script
  - the per-angle verified claim sets returned by the verification-only run

Usage:
    python3 build_synthesis_input.py <verification-run-result.json>

Then run the synthesis with:
    Workflow({scriptPath: "notes/research-cache/synthesize.js",
              args: <contents of synthesis-input.json>})
"""
import json
import sys
import pathlib

HERE = pathlib.Path(__file__).parent
SCRIPT = HERE / "sustag-future-work-wf_0dd2bcea-c63.js"


def template_literal(text, name):
    """Extract the body of `const <name> = ` ... ` `, honoring escaped backticks."""
    marker = f"const {name} = `"
    i = text.index(marker) + len(marker)
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "`":
            break
        out.append(ch)
        i += 1
    return "".join(out)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = SCRIPT.read_text()
    project = template_literal(src, "PROJECT")
    rules = template_literal(src, "RULES")

    raw = pathlib.Path(sys.argv[1]).read_text().strip()
    # The run result may be bare JSON or wrapped in notification text; find the JSON object.
    start = raw.index("{")
    result = json.loads(raw[start:])
    angles = result.get("angles") or result.get("final", {}).get("angles") or []
    angles = [a for a in angles if a]

    banked_path = HERE / "banked-verified-claims.json"
    banked = json.loads(banked_path.read_text()) if banked_path.exists() else []
    payload = {"project": project, "rules": rules, "angles": angles, "bankedClaims": banked}
    out = HERE / "synthesis-input.json"
    out.write_text(json.dumps(payload, indent=1))

    alive = sum(len(a.get("alive") or []) for a in angles)
    dead = sum(len(a.get("dead") or []) for a in angles)
    print(f"wrote {out}")
    print(f"  angles       : {len(angles)}")
    print(f"  claims alive : {alive}")
    print(f"  claims dead  : {dead}")
    print(f"  banked 3-vote claims: {len(banked)} ({sum(1 for b in banked if b.get('survivedMajority')) } surviving)")
    print(f"  project chars: {len(project)}, rules chars: {len(rules)}")
    if not angles:
        print("  WARNING: no angles recovered - inspect the run result before synthesizing")


if __name__ == "__main__":
    main()
