"""Classify common payment and seller failures into readable diagnostics."""


def diagnose_error(message: str) -> str:
    """Return a short diagnosis for common failure patterns."""
    text = (message or "").lower()

    if not text:
        return ""

    if (
        "base-sepolia.infura.io" in text
        or "status: 429" in text
        or "too many requests" in text
    ):
        return (
            "Likely shared Nevermined sandbox / Base Sepolia provider rate limit "
            "(Infura 429). This is usually not seller-specific."
        )

    if "error generating x402 access token" in text:
        return (
            "x402 token generation failed in the shared payments backend. "
            "If multiple sellers fail the same way, treat this as infrastructure first."
        )

    if "unable to get plan balance" in text or "unable to order plan" in text:
        return (
            "Nevermined plan lookup/order failed before the seller request completed. "
            "This points to payment infrastructure or chain access, not just the seller."
        )

    if "plan is not associated to the agent" in text or "plan not found" in text:
        return (
            "Seller payment metadata looks mismatched. The plan ID and agent ID likely "
            "do not belong together in this environment."
        )

    if "cannot connect to seller" in text or "cannot connect to agent" in text:
        return "Seller endpoint is unreachable."

    if "http 404" in text or "route get:/" in text or "not found" in text:
        return (
            "Seller endpoint or route shape does not match the expected A2A/x402 contract."
        )

    if "http 405" in text:
        return (
            "Seller is alive but the route or method does not match the expected contract."
        )

    if "payment required" in text or "http 402" in text:
        return (
            "Seller rejected the payment token or requires a different payment setup."
        )

    if "agent task failed" in text or "state=failed" in text:
        return "Seller agent accepted the request but failed during its own execution."

    return ""
