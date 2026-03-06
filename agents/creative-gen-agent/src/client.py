"""HTTP x402 client demo for the creative generation selling agent."""

import base64
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import httpx

from payments_py import PaymentOptions, Payments

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:3000")
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
    """Decode base64-encoded JSON from headers."""
    json_str = base64.b64decode(base64_str).decode("utf-8")
    return json.loads(json_str)


def pretty_json(obj: dict) -> str:
    """Format JSON for console output."""
    return json.dumps(obj, indent=2)


def main():
    """Run the x402 HTTP payment flow demo."""
    prompt = (
        "Create a brand strategy for a modern AI assistant that helps small law firms "
        "draft client follow-ups and intake emails."
    )

    print("=" * 60)
    print("x402 HTTP Payment Flow - Creative Generation Selling Agent")
    print("=" * 60)
    print(f"\nServer: {SERVER_URL}")
    print(f"Plan ID: {NVM_PLAN_ID}")

    with httpx.Client(timeout=90.0) as client:
        print("\n" + "=" * 60)
        print("STEP 1: Discover pricing tiers")
        print("=" * 60)
        pricing_resp = client.get(f"{SERVER_URL}/pricing")
        print(f"\nGET /pricing -> {pricing_resp.status_code}")
        print(pretty_json(pricing_resp.json()))

        print("\n" + "=" * 60)
        print("STEP 2: Request without payment token")
        print("=" * 60)
        response1 = client.post(
            f"{SERVER_URL}/creative",
            headers={"Content-Type": "application/json"},
            json={"query": prompt},
        )
        print(f"\nPOST /creative -> {response1.status_code} {response1.reason_phrase}")

        if response1.status_code != 402:
            print(f"Expected 402 Payment Required, got: {response1.status_code}")
            sys.exit(1)

        print("\n" + "=" * 60)
        print("STEP 3: Decode payment requirements")
        print("=" * 60)
        payment_required_header = response1.headers.get("payment-required")
        if not payment_required_header:
            print("Missing 'payment-required' header in 402 response")
            sys.exit(1)
        payment_required = decode_base64_json(payment_required_header)
        print(pretty_json(payment_required))

        print("\n" + "=" * 60)
        print("STEP 4: Generate x402 access token")
        print("=" * 60)
        token_result = payments.x402.get_x402_access_token(NVM_PLAN_ID)
        access_token = token_result["accessToken"]
        print(f"Token generated. Length: {len(access_token)} chars")

        print("\n" + "=" * 60)
        print("STEP 5: Request with payment token")
        print("=" * 60)
        response2 = client.post(
            f"{SERVER_URL}/creative",
            headers={
                "Content-Type": "application/json",
                "payment-signature": access_token,
            },
            json={"query": prompt},
        )
        print(f"\nPOST /creative -> {response2.status_code} {response2.reason_phrase}")

        if response2.status_code != 200:
            print(f"Expected 200 OK, got: {response2.status_code}")
            print(f"Response: {response2.text}")
            sys.exit(1)

        print("\nResponse body:")
        print(pretty_json(response2.json()))

        print("\n" + "=" * 60)
        print("STEP 6: Check usage analytics")
        print("=" * 60)
        stats_resp = client.get(f"{SERVER_URL}/stats")
        print(f"\nGET /stats -> {stats_resp.status_code}")
        print(pretty_json(stats_resp.json()))


if __name__ == "__main__":
    main()
