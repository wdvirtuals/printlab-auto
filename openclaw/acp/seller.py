"""Register PrintLab ACP seller offerings with Virtuals Protocol.

Run once to publish offerings to the ACP marketplace:
    python3 openclaw/acp/seller.py

Requires:
    - VIRTUALS_API_KEY env var (from Virtuals Protocol dashboard)
    - PrintLab API running (API_PORT set in .env)
"""

import json
import os
import sys
from pathlib import Path

import httpx

OFFERINGS_FILE = Path(__file__).parent / "offerings.json"
ACP_REGISTRY_URL = "https://acp.virtuals.io/api/v1/offerings"


def main():
    api_key = os.getenv("VIRTUALS_API_KEY")
    if not api_key:
        print("Error: Set VIRTUALS_API_KEY environment variable")
        print("Get your key from: https://app.virtuals.io/developer")
        sys.exit(1)

    api_base = os.getenv("API_BASE_URL", "http://localhost:8000")

    with open(OFFERINGS_FILE) as f:
        data = json.load(f)

    seller = data["seller"]
    offerings = data["offerings"]

    print(f"Registering {len(offerings)} offerings for: {seller['name']}")

    with httpx.Client(
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    ) as client:
        for offering in offerings:
            # Set the full endpoint URL
            endpoint = offering.pop("endpoint", "")
            offering["endpoint_url"] = f"{api_base}{endpoint.split(' ', 1)[-1]}"
            offering["endpoint_method"] = endpoint.split(" ")[0]
            offering["seller_name"] = seller["name"]
            offering["seller_description"] = seller["description"]

            try:
                resp = client.post(ACP_REGISTRY_URL, json=offering)
                if resp.status_code in (200, 201):
                    print(f"  Registered: {offering['name']}")
                else:
                    print(f"  Failed ({resp.status_code}): {offering['name']}")
                    print(f"    {resp.text}")
            except Exception as e:
                print(f"  Error registering {offering['name']}: {e}")

    print("\nDone. Offerings are now discoverable on the ACP marketplace.")


if __name__ == "__main__":
    main()
