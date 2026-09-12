"""Licensing - DISABLED for this evaluation / shareable build.

The full product uses an offline signed license key. This evaluation build ships
with licensing turned off so it can be run and tested without a key. The public
API (check / install_token / verify_token) is preserved so the app imports and
runs unchanged.
"""


class LicenseError(Exception):
    pass


def verify_token(token: str) -> dict:
    return {"customer": "Evaluation", "license_id": "eval", "expires": "2099-12-31"}


def install_token(token: str):
    # No-op in the evaluation build.
    return None


def check() -> dict:
    """Always active in the evaluation build (no license required)."""
    return {
        "state": "active",
        "message": "Evaluation build - licensing disabled.",
        "customer": "Evaluation",
        "license_id": "eval",
        "expires": "2099-12-31",
        "days_left": 99999,
    }
