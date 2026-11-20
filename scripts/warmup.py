"""Warm the exact shared policy prefix before the presentation."""

import argparse
import getpass
import os

import httpx

from scripts.smoke_test import validate_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    code = os.getenv("DEMO_ACCESS_CODE") or getpass.getpass("Demo access code: ")
    with httpx.Client(base_url=args.base_url, headers={"X-Demo-Code": code}, timeout=45) as client:
        result = validate_run(client, "prompt_caching", "default", 5, "live", False, False)
    if result["failures"]:
        raise SystemExit("\n".join(result["failures"]))
    print(f"Prefix warmup observed {result['summary']['candidate']['tokens']['cached']} cached input tokens.")


if __name__ == "__main__":
    main()
