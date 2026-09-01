"""`python -m data.insights` -- regenerate the report."""

from __future__ import annotations

import argparse

from . import config, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tier", type=int, choices=(0, 1, 2), help="run one tier only (unimplemented tiers skip)")
    ap.parse_args(argv)
    report.write()
    print(f"  wrote {config.REPORT.relative_to(config.HERE.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
