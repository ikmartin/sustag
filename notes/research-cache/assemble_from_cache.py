#!/usr/bin/env python3
"""Assemble synthesis-input.json directly from the on-disk agent cache.

No workflow re-run required, and no LLM cost. Recovers, per angle:
  - angle metadata (key/title/question/good) parsed from the workflow script
  - every extracted claim, attributed to its angle via the extraction agent's prompt
  - tier-1 claims (3-lens verified) from banked-verified-claims.json

Produces a three-tier evidence payload for synthesize.js:
  tier 1  alive       - verified, survived majority vote
  tier 1  dead        - verified, refuted (passed through as corrections)
  tier 3  unverified  - everything else (coverage/leads only)

Usage:  python3 assemble_from_cache.py
"""
import json
import glob
import os
import pathlib
import re

HERE = pathlib.Path(__file__).parent
SCRIPT = HERE / "sustag-future-work-wf_0dd2bcea-c63.js"
DIRS = [
    "/Users/isaac/.claude/projects/-Users-isaac-dev-sustag/e3f1f490-6fb8-475d-be0e-c0cebf530519/subagents/workflows/wf_0dd2bcea-c63",
    "/Users/isaac/.claude/projects/-Users-isaac-dev-sustag/408994b9-8d9b-485d-9731-ba0bfa7507ce/subagents/workflows/wf_0dd2bcea-c63",
]


def literal(text, start, name):
    """Read a `name: <quoted>` field starting at/after `start`. Handles ' and `."""
    m = re.compile(rf"\b{name}:\s*([`'])").search(text, start)
    if not m:
        return None
    q = m.group(1)
    i = m.end()
    out = []
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
            continue
        if ch == q:
            break
        out.append(ch)
        i += 1
    return "".join(out)


def parse_angles(src):
    start = src.index("const ANGLES = [")
    end = src.index("\n]", start)
    block = src[start:end]
    angles = []
    for m in re.finditer(r"\bkey:\s*'", block):
        i = m.start()
        angles.append(
            {
                "angleKey": literal(block, i, "key"),
                "angleTitle": literal(block, i, "title"),
                "question": literal(block, i, "question"),
                "good": literal(block, i, "good"),
            }
        )
    return angles


def load_cache():
    """Return (agentId -> result) and (agentId -> first-prompt) across all run dirs."""
    results, prompts = {}, {}
    for d in DIRS:
        jp = os.path.join(d, "journal.jsonl")
        if os.path.exists(jp):
            for line in open(jp):
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") == "result" and isinstance(e.get("result"), dict):
                    results.setdefault(e["agentId"], e["result"])
        for f in glob.glob(os.path.join(d, "agent-*.jsonl")):
            aid = os.path.basename(f)[6:-6]
            if aid in prompts:
                continue
            with open(f, errors="ignore") as fh:
                first = fh.readline()
            try:
                c = json.loads(first).get("message", {}).get("content")
            except Exception:
                continue
            if isinstance(c, str):
                prompts[aid] = c
    return results, prompts


def norm(t):
    return re.sub(r"\W+", " ", (t or "").lower()).strip()[:200]


def main():
    src = SCRIPT.read_text()
    angles = parse_angles(src)
    by_title = {a["angleTitle"]: a for a in angles}
    for a in angles:
        a["unverified"], a["alive"], a["dead"] = [], [], []

    results, prompts = load_cache()

    # attribute every extracted claim to its angle via the extraction prompt
    anglepat = re.compile(r"### Research angle\n(.+?)\n", re.S)
    unattributed = 0
    for aid, res in results.items():
        if "claims" not in res:
            continue
        p = prompts.get(aid, "")
        m = anglepat.search(p)
        angle = by_title.get(m.group(1).strip()) if m else None
        if angle is None:
            unattributed += len(res.get("claims") or [])
            continue
        for c in res.get("claims") or []:
            angle["unverified"].append({"claim": c})

    # promote tier-1 verified claims out of the unverified pool
    banked_path = HERE / "banked-verified-claims.json"
    banked = json.loads(banked_path.read_text()) if banked_path.exists() else []
    bank_by_text = {norm(b["claim"]): b for b in banked}
    promoted = 0
    for a in angles:
        keep = []
        for item in a["unverified"]:
            b = bank_by_text.get(norm(item["claim"].get("text")))
            if b is None:
                keep.append(item)
                continue
            promoted += 1
            entry = {"claim": item["claim"], "votes": b["votes"], "nVotes": b["nVotes"], "kills": b["kills"]}
            (a["alive"] if b["survivedMajority"] else a["dead"]).append(entry)
        a["unverified"] = keep

    project = literal_block(src, "PROJECT")
    rules = literal_block(src, "RULES")
    payload = {"project": project, "rules": rules, "angles": angles, "bankedClaims": banked}
    out = HERE / "synthesis-input.json"
    out.write_text(json.dumps(payload, indent=1))

    print(f"wrote {out}  ({out.stat().st_size // 1024} KB)")
    print(f"  angles parsed        : {len(angles)}")
    print(f"  tier-1 alive         : {sum(len(a['alive']) for a in angles)}")
    print(f"  tier-1 refuted       : {sum(len(a['dead']) for a in angles)}")
    print(f"  tier-3 unverified    : {sum(len(a['unverified']) for a in angles)}")
    print(f"  banked claims matched: {promoted}/{len(banked)}")
    if unattributed:
        print(f"  WARNING: {unattributed} claims could not be attributed to an angle (dropped)")
    for a in angles:
        print(f"    {a['angleKey']:26s} alive {len(a['alive']):3d}  refuted {len(a['dead']):3d}  unverified {len(a['unverified']):4d}")


def literal_block(text, name):
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


if __name__ == "__main__":
    main()
