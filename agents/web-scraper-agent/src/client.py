"""
HTTP x402 client - demonstrates the payment flow for the web scraper agent.

Usage:
    # First start the server: poetry run agent
    # Then: poetry run client
"""

import base64
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import httpx

from payments_py import Payments, PaymentOptions

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:3020")
NVM_API_KEY = os.getenv("NVM_API_KEY", "")
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
NVM_PLAN_ID = os.getenv("NVM_PLAN_ID", "")

if not NVM_API_KEY or not NVM_PLAN_ID:
    print("NVM_API_KEY and NVM_PLAN_ID are required.")
    sys.exit(1)

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)


def decode_base64_json(base64_str: str) -> dict:
    json_str = base64.b64decode(base64_str).decode("utf-8")
    return json.loads(json_str)


def pretty_json(obj: dict) -> str:
    return json.dumps(obj, indent=2)


def main():
    print("=" * 60)
    print("x402 Payment Flow - Web Scraper Agent")
    print("=" * 60)
    print(f"\nServer: {SERVER_URL}")
    print(f"Plan ID: {NVM_PLAN_ID}")

    with httpx.Client(timeout=60.0) as client:
        # Step 1: Discover pricing
        print("\n" + "=" * 60)
        print("STEP 1: Discover pricing tiers")
        print("=" * 60)
        pricing_resp = client.get(f"{SERVER_URL}/pricing")
        print(f"\nGET /pricing -> {pricing_resp.status_code}")
        print(pretty_json(pricing_resp.json()))

        # Step 2: Request without token -> 402
        print("\n" + "=" * 60)
        print("STEP 2: Request without payment token")
        print("=" * 60)
        response1 = client.post(
            f"{SERVER_URL}/data",
            json={"query": "Scrape https://example.com and extract the main content"},
        )
        print(f"\nPOST /data -> {response1.status_code}")

        if response1.status_code != 402:
            print(f"Expected 402, got: {response1.status_code}")
            sys.exit(1)

        # Step 3: Decode payment requirements
        print("\n" + "=" * 60)
        print("STEP 3: Decode payment requirements")
        print("=" * 60)
        payment_required_header = response1.headers.get("payment-required")
        if not payment_required_header:
            print("Missing 'payment-required' header")
            sys.exit(1)
        payment_required = decode_base64_json(payment_required_header)
        print(pretty_json(payment_required))

        # Step 4: Generate token
        print("\n" + "=" * 60)
        print("STEP 4: Generate x402 access token")
        print("=" * 60)
        token_result = payments.x402.get_x402_access_token(NVM_PLAN_ID)
        access_token = token_result["accessToken"]
        print(f"Token generated! Length: {len(access_token)} chars")

        # Step 5: Request with token
        print("\n" + "=" * 60)
        print("STEP 5: Request with payment token")
        print("=" * 60)
        response2 = client.post(
            f"{SERVER_URL}/data",
            headers={"payment-signature": access_token},
            json={"query": "Scrape https://example.com and extract the main content"},
        )
        print(f"\nPOST /data -> {response2.status_code}")
        if response2.status_code == 200:
            print(pretty_json(response2.json()))

        # Step 6: Check stats
        print("\n" + "=" * 60)
        print("STEP 6: Check analytics")
        print("=" * 60)
        stats_resp = client.get(f"{SERVER_URL}/stats")
        print(pretty_json(stats_resp.json()))

        print("\n" + "=" * 60)
        print("FLOW COMPLETE!")
        print("=" * 60)


if __name__ == "__main__":
    main()
