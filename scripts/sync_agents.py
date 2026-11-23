"""Read back or publish the app's six Foundry Prompt Agents with an Azure CLI identity."""

import argparse
import os

import httpx
from azure.identity import AzureCliCredential

from app.agents import active_version, catalog
from app.settings import Settings, valid_project_endpoint


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", default=os.environ.get("FOUNDRY_PROJECT_ENDPOINT", ""))
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    parser.add_argument("--apply", action="store_true", help="Publish when a definition does not match.")
    opts = parser.parse_args()
    if not valid_project_endpoint(opts.project_endpoint):
        parser.error("Use an HTTPS Foundry project endpoint under services.ai.azure.com.")
    cfg = Settings(foundry_project_endpoint=opts.project_endpoint)
    credential = AzureCliCredential(tenant_id=opts.tenant_id or "")
    token = credential.get_token("https://ai.azure.com/.default")
    with httpx.Client(base_url=opts.project_endpoint.rstrip("/") + "/", timeout=45,
                      headers={"Authorization": f"Bearer {token.token}"}, follow_redirects=False) as client:
        for spec in catalog(cfg).values():
            response = client.get(f"agents/{spec.name}", params={"api-version": "v1"})
            if response.status_code != 404:
                response.raise_for_status()
            try:
                version = active_version(response.json() if response.status_code == 200 else {}, spec)
            except ValueError as exc:
                if not opts.apply:
                    raise SystemExit(str(exc)) from None
                if response.status_code == 200:
                    print(f"{spec.name}: publishing an updated definition.", flush=True)
                exists = response.status_code == 200
                path = f"agents/{spec.name}/versions" if exists else "agents"
                definition = {"description": spec.description, "definition": spec.definition()}
                response = client.post(path, params={"api-version": "v1"},
                                       json=definition if exists else {"name": spec.name, **definition})
                if response.is_error:
                    problem = response.json().get("error", {}).get("message", response.reason_phrase)
                    raise SystemExit(f"{spec.name}: HTTP {response.status_code} {problem}")
                response = client.get(f"agents/{spec.name}", params={"api-version": "v1"})
                response.raise_for_status()
                version = active_version(response.json(), spec)
            print(f"{spec.name} v{version} | {spec.deployment}", flush=True)


if __name__ == "__main__":
    main()
