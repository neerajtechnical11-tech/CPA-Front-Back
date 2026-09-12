"""Privacy controls: PII redaction and a data-handling notice.

When enabled (appsettings: privacy_redact_pii), free text is redacted before it
is sent to the Azure OpenAI chat model for gap narratives, reducing the personal
data that leaves the machine. Embeddings still require raw text; the notice below
explains exactly what is processed where.
"""
import re

from backend import appsettings

NOTICE = (
    "Data handling: uploaded documents are processed on this machine. Text embeddings "
    "and (if enabled) redacted excerpts for gap narratives are sent to Azure OpenAI within "
    "your Azure tenant. Documents are not stored in the cloud. Assessment results are not "
    "persisted unless enabled by an administrator."
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\b\+?\d[\d\s\-()]{6,}\d\b")
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_LONGNUM = re.compile(r"\b\d{7,}\b")


def redaction_enabled() -> bool:
    return appsettings.get_bool("privacy_redact_pii")


def redact(text: str) -> str:
    """Mask emails, phone numbers, IPs and long digit sequences."""
    if not text:
        return text
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _IP.sub("[REDACTED_IP]", text)
    text = _PHONE.sub("[REDACTED_PHONE]", text)
    text = _LONGNUM.sub("[REDACTED_NUMBER]", text)
    return text


def redact_if_enabled(text: str) -> str:
    return redact(text) if redaction_enabled() else text
