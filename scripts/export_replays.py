"""Export actual live recordings for the offline presenter copy."""

import argparse
import getpass
import os
from pathlib import Path

import httpx

from app.guards import save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", default=Path("replays"), type=Path)
    args = parser.parse_args()
    code = os.getenv("DEMO_ACCESS_CODE") or getpass.getpass("Demo access code: ")
    count = 0
    missing = []
    with httpx.Client(base_url=args.base_url, headers={"X-Demo-Code": code}, timeout=30) as client:
        config = client.get("/api/config")
        config.raise_for_status()
        for lever, tab in config.json()["tabs"].items():
            for mode in tab["modes"]:
                response = client.get(f"/api/replays/{lever}/{mode}/recording")
                if response.status_code == 404:
                    missing.append(f"{lever}/{mode}")
                    continue
                response.raise_for_status()
                recording = response.json()
                if recording["source"] != "live" or not recording["summary"]["complete"]:
                    raise SystemExit("Refusing to export an incomplete or synthetic recording.")
                save_json(args.output / f"{lever}-{mode}.json", recording)
                count += 1
    print(f"Exported {count} complete live recordings.")
    if missing:
        raise SystemExit("No complete live recording for: " + ", ".join(missing))


if __name__ == "__main__":
    main()
