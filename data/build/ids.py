"""The published-id mint: base-36 SNR codes and the append-only id ledger.

WHY MANUFACTURED IDS. Publishing a merged, scrubbed record under a source's own uid invites the reader to assume it IS the source's series; the `SNR-` prefix says "this is ours, processed". Codes are `SNR-` + four base-36 digits (0-9A-Z, uppercase), counting from SNR-0001 -- SNR-0000 is never assigned -- for a namespace of 1,679,615 against ~20,000 registrations. MERGES MINT NOTHING: a merged record publishes under an id its canonical member already owns, so no decision ever consumes or retires a code; capacity is spent by registration alone, once per sensor, ever.

THE LEDGER, NOT A SORT. A code is minted once, in `decisions/ids.csv`, and never re-derived: an id defined as "position in the sorted table" would renumber the whole cohort every time a sensor arrives. The initial mint (the christening -- nothing special, just the first mint over the whole cohort) is sorted `(lat, lon, uid)` for rough geographic locality; every later batch takes the next free codes in the same deterministic sort WITHIN the batch. Sensors with no coordinates sort last within their batch by uid. The ledger is durable input-state exactly like the decision ledgers -- a build reads it, appends codes for unseen registrations, and the appended state feeds every later build, which is why wipe-and-rebuild reproduces identical ids. Its loss is the one event that renumbers, which is why it is git-tracked; recovery from deletion is a git restore, never a re-mint. Single-writer, like every ledger: concurrent mints on two clones are a git conflict resolved by hand.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd

from . import config

PREFIX = "SNR-"
WIDTH = 4
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
CAPACITY = 36 ** WIDTH - 1                      # SNR-0001 .. SNR-ZZZZ
_CODE_RE = re.compile(r"^SNR-[0-9A-Z]{4}$")

COLUMNS = ("snr_id", "uid", "minted_under")


def ledger_path() -> Path:
    return config.DECISIONS / "ids.csv"


def encode(n: int) -> str:
    """Ordinal -> code. Counting starts at 1; 0 and out-of-range raise."""
    if not (1 <= int(n) <= CAPACITY):
        raise ValueError(f"ordinal {n} outside [1, {CAPACITY}]")
    n = int(n)
    digits = []
    for _ in range(WIDTH):
        n, r = divmod(n, 36)
        digits.append(_ALPHABET[r])
    return PREFIX + "".join(reversed(digits))


def decode(code: str) -> int:
    """Code -> ordinal. Strict: uppercase, fixed width, the exact prefix."""
    if not _CODE_RE.match(str(code)):
        raise ValueError(f"malformed SNR code {code!r}")
    n = 0
    for ch in str(code)[len(PREFIX):]:
        n = n * 36 + _ALPHABET.index(ch)
    if n == 0:
        raise ValueError("SNR-0000 is never assigned")
    return n


def load() -> pd.DataFrame:
    """The id ledger as strings; empty frame with the contract columns when no mint has happened."""
    p = ledger_path()
    if not p.exists():
        return pd.DataFrame(columns=list(COLUMNS))
    d = pd.read_csv(p, dtype=str).fillna("")
    missing = [c for c in COLUMNS if c not in d.columns]
    if missing:
        raise ValueError(f"{p.name} lacks column(s) {missing}")
    return d


def mapping() -> dict[str, str]:
    """uid -> snr_id for every minted registration."""
    d = load()
    return dict(zip(d.uid, d.snr_id))


def mint_new(batch: pd.DataFrame, minted_under: str) -> dict[str, str]:
    """Assign the next free codes to every registration in `batch` not already minted; returns the NEW uid -> code assignments.

    `batch` needs columns `site_uid`, `lat`, `lon`. The batch is sorted `(lat, lon, site_uid)` before assignment -- null coordinates last -- so a mint is reproducible whatever order discovery iterated in. Appends to the ledger atomically; existing rows are never touched. Refuses duplicate uids in the batch, warns past half capacity.
    """
    if batch.site_uid.duplicated().any():
        dup = batch.site_uid[batch.site_uid.duplicated()].unique().tolist()
        raise ValueError(f"mint batch contains duplicate uids: {dup[:8]}")
    existing = load()
    known = set(existing.uid)
    fresh = batch[~batch.site_uid.isin(known)]
    if not len(fresh):
        return {}
    fresh = fresh.sort_values(
        ["lat", "lon", "site_uid"], na_position="last", kind="mergesort").reset_index(drop=True)

    start = 1 + (max(decode(c) for c in existing.snr_id) if len(existing) else 0)
    if start + len(fresh) - 1 > CAPACITY:
        raise ValueError(f"id namespace exhausted: need {len(fresh)} codes from {start}, capacity {CAPACITY}")
    if start + len(fresh) - 1 > CAPACITY // 2:
        print(f"  WARNING: id namespace past half capacity ({start + len(fresh) - 1:,} of {CAPACITY:,})")

    new = pd.DataFrame({
        "snr_id": [encode(start + i) for i in range(len(fresh))],
        "uid": fresh.site_uid.astype(str).tolist(),
        "minted_under": str(minted_under),
    })
    out = pd.concat([existing, new], ignore_index=True) if len(existing) else new
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    out.to_csv(tmp, index=False)
    os.replace(tmp, p)
    return dict(zip(new.uid, new.snr_id))


def validate(d: pd.DataFrame | None = None) -> list[str]:
    """Ledger-contract violations: format, bijection, monotone codes, no reuse. Empty means conforming.

    Append-only-vs-git-history is the verify layer's job (it needs the tracked baseline); this validates the file on its own terms.
    """
    d = load() if d is None else d
    bad = []
    if not len(d):
        return bad
    malformed = [c for c in d.snr_id if not _CODE_RE.match(str(c))]
    if malformed:
        bad.append(f"malformed codes: {malformed[:8]}")
        return bad
    for col in ("snr_id", "uid"):
        dup = d[col][d[col].duplicated()].unique().tolist()
        if dup:
            bad.append(f"{col} not unique -- the bijection is broken: {dup[:8]}")
    ords = [decode(c) for c in d.snr_id]
    if ords != sorted(ords):
        bad.append("codes are not in mint order -- the ledger was rewritten, not appended")
    if len(set(ords)) == len(ords) and ords and ords[-1] != len(ords):
        bad.append(f"codes are not contiguous from SNR-0001: {len(ords)} rows end at ordinal {ords[-1]}")
    return bad


def append_only_vs_git() -> tuple[bool | None, str]:
    """Existing rows byte-identical to the tracked baseline; appends are the only legal diff. SKIPs (None) until the ledger has a committed baseline."""
    import subprocess

    p = ledger_path()
    if not p.exists():
        return None, "no id ledger yet"
    rel = p.relative_to(config.REPO)
    try:
        head = subprocess.run(["git", "-C", str(config.REPO), "show", f"HEAD:{rel}"],
                              capture_output=True, text=True, timeout=30)
    except Exception as e:
        return None, f"git unavailable ({type(e).__name__})"
    if head.returncode != 0:
        return None, "no committed baseline yet -- the check activates on the ledger's first commit"
    baseline = head.stdout
    current = p.read_text()
    if current == baseline:
        return True, "identical to the committed baseline"
    if current.startswith(baseline.rstrip("\n") + "\n") or current.startswith(baseline):
        n_new = current.count("\n") - baseline.count("\n")
        return True, f"{n_new} row(s) appended beyond the committed baseline"
    return False, "the committed prefix was REWRITTEN -- ids may have silently renumbered"
