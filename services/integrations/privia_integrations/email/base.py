"""Shared email helpers: address validation and body safety."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from privia_shared.domain import EmailAddress
from privia_shared.errors import ValidationError

#: Deliberately conservative. PRIVIA would rather refuse an exotic-but-valid
#: address than silently send mail to a malformed one.
_ADDRESS_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_HEADER_INJECTION_RE = re.compile(r"[\r\n\x00]")
MAX_RECIPIENTS = 25
MAX_SUBJECT_CHARS = 300
MAX_BODY_CHARS = 100_000


def parse_address(raw: str) -> EmailAddress:
    """Parse ``Name <a@b.com>`` or ``a@b.com`` and validate it."""
    value = (raw or "").strip()
    if not value:
        raise ValidationError("An email address is required.")
    if _HEADER_INJECTION_RE.search(value):
        raise ValidationError(
            "The address contains a newline, which would allow header injection.",
            details={"address": value[:80]},
        )
    name: str | None = None
    match = re.match(r"^(?P<name>.*?)\s*<(?P<addr>[^>]+)>$", value)
    if match:
        name = match.group("name").strip().strip('"') or None
        value = match.group("addr").strip()
    if not _ADDRESS_RE.match(value):
        raise ValidationError(
            f"'{value[:80]}' is not a valid email address.", details={"address": value[:80]}
        )
    if len(value) > 254:
        raise ValidationError("That email address is too long.")
    return EmailAddress(address=value.lower(), name=name)


def parse_addresses(values: Iterable[str] | str | None) -> tuple[EmailAddress, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = [v for v in re.split(r"[;,]", values) if v.strip()]
    parsed = tuple(parse_address(v) for v in values)
    if len(parsed) > MAX_RECIPIENTS:
        raise ValidationError(
            f"That is {len(parsed)} recipients; PRIVIA sends to at most {MAX_RECIPIENTS} at once."
        )
    return parsed


def validate_subject(subject: str) -> str:
    cleaned = (subject or "").strip()
    if _HEADER_INJECTION_RE.search(cleaned):
        raise ValidationError("The subject contains a newline, which is not allowed.")
    if len(cleaned) > MAX_SUBJECT_CHARS:
        raise ValidationError(f"The subject is longer than {MAX_SUBJECT_CHARS} characters.")
    return cleaned


def validate_body(body: str) -> str:
    if len(body or "") > MAX_BODY_CHARS:
        raise ValidationError(f"The message body is longer than {MAX_BODY_CHARS} characters.")
    return body or ""


def format_recipients(addresses: Sequence[EmailAddress]) -> str:
    return ", ".join(str(a) for a in addresses)
