"""Measure the exact corpus and bake layout-only counts for offline rendering."""

import argparse
import json

from app.prompts import ROLE_INSTRUCTIONS, estimate, stable_prefix
from app.settings import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-layout", action="store_true")
    args = parser.parse_args()
    data = ROOT / "app" / "data"
    policies = (data / "policies.md").read_bytes().decode("utf-8")
    requests = json.loads((data / "requests_prompt_caching.json").read_text(encoding="utf-8"))
    prefix = stable_prefix(policies)
    stats = {
        "system": estimate(ROLE_INSTRUCTIONS), "policies": estimate(policies),
        "customer": estimate(requests[0]["text"]), "prefix": estimate(prefix),
    }
    if stats["prefix"] < 1024:
        raise SystemExit("Stable prefix is too short to cache.")
    if args.write_layout:
        (ROOT / "app" / "data" / "prefix_layout.json").write_text(json.dumps(stats) + "\n", encoding="utf-8")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
