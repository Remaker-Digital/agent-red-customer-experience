# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Reversible PII tokenization for transport-layer privacy (AGNTCY Phase 6).

Replaces one-way PII scrubbing (``[REDACTED:type]``) with reversible UUID
tokens (``pii-{type}-{uuid}``) that can be detokenized after AI processing.
This ensures PII never reaches external AI models while allowing the original
values to be restored in customer-facing responses.

SPEC-1543: PII Tokenization Before External AI Calls
SPEC-1544: PII Detokenization Before Customer Response
SPEC-1545: PII Token Mapping Storage
SPEC-1546: PII Tokenization Entity Detection

Architecture:
    1. ``tokenize()`` replaces PII with UUID tokens before agent dispatch
    2. AI agents process tokenized text (no PII exposure)
    3. ``detokenize()`` restores original values after Critic validation
    4. Token mappings persisted to Cosmos DB per conversation (7-day TTL)

Entity types detected:
    - email: RFC-compliant email addresses
    - phone: US/international phone numbers (E.164, parenthesized, dashed)
    - order_number: Common e-commerce order ID patterns (#, ORD-, ORDER-)
    - name: Customer names (from customer context, not regex)

(c) 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PII detection patterns (SPEC-1546)
# ---------------------------------------------------------------------------

# S23: these boundaries are ASCII. Python's Unicode word class treats an
# accented letter or a CJK ideograph as a word character, so \b and \w left
# the value, or the start of it, in clear.
_ASCII_BEFORE = r"(?<![A-Za-z0-9_])"
_ASCII_AFTER = r"(?![A-Za-z0-9_])"
_EMAIL_LOCAL_ASCII = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
# Decision 18: Latin accented letters, Greek, and Cyrillic. Not every letter.
# Combining marks stay with a decomposed letter. A CJK ideograph stays outside
# the scan, so surrounding words are not the address.
_EMAIL_EXTRA_RANGES = ((0x00C0, 0x024F), (0x0300, 0x036F), (0x0370, 0x03FF), (0x0400, 0x04FF))
_EMAIL_DOMAIN_RE = re.compile(
    r"[A-Za-z0-9.\-\u00C0-\u024F\u0300-\u036F\u0370-\u03FF\u0400-\u04FF]+"
    r"\.[A-Za-z\u00C0-\u024F\u0300-\u036F\u0370-\u03FF\u0400-\u04FF]{2,}(?![A-Za-z0-9_])"
)


def _email_local_char(char: str) -> bool:
    """ASCII email punctuation, or a Latin, Greek, or Cyrillic letter."""
    if char in _EMAIL_LOCAL_ASCII:
        return True
    code = ord(char)
    return any(start <= code <= end for start, end in _EMAIL_EXTRA_RANGES)


def _find_email_spans(text: str) -> Iterator[tuple[int, int]]:
    """Find each email from its @.

    A long accented run with no @ is one forward scan. Searching the local-part
    class from every character was quadratic (S23 review).
    """
    start = 0
    while True:
        at = text.find("@", start)
        if at < 0:
            return
        left = at
        while left > 0 and _email_local_char(text[left - 1]):
            left -= 1
        if left < at:
            domain = _EMAIL_DOMAIN_RE.match(text, at + 1)
            if domain is not None:
                yield left, domain.end()
        start = at + 1

# Phone: US, international, parenthesized area codes
_PHONE_RE = re.compile(
    _ASCII_BEFORE
    + r"(?:"
    + r"\+?\d{1,3}[\s\-]?"  # Country code
    + r"(?:\(\d{1,4}\)|\d{1,4})"  # Area code (optional parens)
    + r"[\s\-]?"
    + r"\d{3,4}[\s\-]?\d{3,4}"  # Subscriber number
    + r"|"
    + r"\(\d{3}\)\s*\d{3}[\-]\d{4}"  # (555) 123-4567
    + r"|"
    + r"\d{3}[\-\.]\d{3}[\-\.]\d{4}"  # 555-123-4567 or 555.123.4567
    + r")"
    + _ASCII_AFTER
)

# Order numbers: #12345, ORD-12345, ORDER-12345, etc.
# Case is spelled out. re.IGNORECASE would make the ASCII lookaround match
# letters such as dotted I, long s, and the Kelvin sign (S23 review).
_ORDER_RE = re.compile(
    _ASCII_BEFORE
    + r"(?:#\d{4,10}|"
    + r"(?:[Oo][Rr][Dd](?:[Ee][Rr])?|[Ii][Nn][Vv](?:[Oo][Ii][Cc][Ee])?)"
    + r"(?:[\-_]\d{4,10}|\d{4,10})(?:[\-_]\d{2,10})*)"
    + _ASCII_AFTER
)

# A token issued by PiiTokenStore.add: pii-{entity_type}-{12 hex characters}
_TOKEN_RE = re.compile(r"pii-[a-z_]+-[0-9a-f]{12}")


# ---------------------------------------------------------------------------
# Token mapping storage
# ---------------------------------------------------------------------------

@dataclass
class TokenMapping:
    """Maps a PII token back to its original value.

    Attributes:
        token: The replacement token (e.g., "pii-email-a1b2c3d4").
        original: The original PII value.
        entity_type: PII category (email, phone, order_number, name).
        conversation_id: Scoped to this conversation.
        tenant_id: Owning tenant.
    """

    token: str
    original: str
    entity_type: str
    conversation_id: str
    tenant_id: str


@dataclass
class PiiTokenStore:
    """In-memory cache of PII token mappings for a conversation.

    Token mappings are cached per conversation. Cosmos DB persistence
    is handled asynchronously for durability (SPEC-1545).
    """

    conversation_id: str
    tenant_id: str
    mappings: dict[str, TokenMapping] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)

    def add(self, original: str, entity_type: str) -> str:
        """Add a PII value and return its token. Deduplicates."""
        if original in self._reverse:
            return self._reverse[original]

        token = f"pii-{entity_type}-{uuid.uuid4().hex[:12]}"
        while token in self.mappings:  # 48 random bits; never overwrite a mapping on a collision
            token = f"pii-{entity_type}-{uuid.uuid4().hex[:12]}"

        mapping = TokenMapping(
            token=token,
            original=original,
            entity_type=entity_type,
            conversation_id=self.conversation_id,
            tenant_id=self.tenant_id,
        )
        self.mappings[token] = mapping
        self._reverse[original] = token
        return token

    def resolve(self, token: str) -> str | None:
        """Resolve a token back to its original value."""
        mapping = self.mappings.get(token)
        return mapping.original if mapping else None

    def to_cosmos_docs(self) -> list[dict[str, Any]]:
        """Serialize mappings for Cosmos DB persistence."""
        return [
            {
                "id": f"pii-{m.conversation_id}-{m.token}",
                "token": m.token,
                "original": m.original,
                "entity_type": m.entity_type,
                "conversation_id": m.conversation_id,
                "tenant_id": m.tenant_id,
                "ttl": 604800,  # 7 days
            }
            for m in self.mappings.values()
        ]


# ---------------------------------------------------------------------------
# PII Tokenizer (SPEC-1543, SPEC-1544)
# ---------------------------------------------------------------------------

# Per-conversation token stores (in-memory cache)
_conversation_stores: dict[str, PiiTokenStore] = {}


# Regex detectors, in detection order after customer names, each with the character the
# next search may resume after (see _find_all). An email match that starts inside an
# earlier one's local part ends at the same "@" and domain, so it adds nothing.
_DETECTORS: tuple[tuple[str, re.Pattern[str] | None, str | None], ...] = (
    ("email", None, "@"),
    ("phone", _PHONE_RE, None),
    ("order_number", _ORDER_RE, None),
)

# A detected value: (start, end) in the text, its length as read, and its entity type.
_Candidate = tuple[int, int, int, str]

# Decision 12. A stored name also matches its surname, its "Last, First" form,
# a non-breaking space, and the composed or decomposed spelling. The boundary
# is outside the case-insensitive part so it stays ASCII-and-Latin, the same
# limit as the order-number lookaround. An @ after the word is an email.
_NAME_CHAR = r"A-Za-z0-9_\u00C0-\u024F\u0300-\u036F"


def _accent_forms(part: str) -> str:
    """One part, matching its stored spelling and the composed and decomposed forms."""
    forms = {part, unicodedata.normalize("NFC", part), unicodedata.normalize("NFD", part)}
    return "(?:" + "|".join(re.escape(form) for form in forms) + ")"


def _name_parts(name: str) -> list[str]:
    """The words of a stored name. A non-breaking space is a separator."""
    flat = name.replace("\u00a0", " ").strip()
    return [part for part in re.split(r"\s+", flat) if part]


def _customer_name_patterns(names: list[str]) -> list[re.Pattern[str]]:
    """Patterns for the stored name, its given name, its surname, and the decision 12 forms."""
    patterns: list[re.Pattern[str]] = []
    gap = r"[\s\u00a0]+"
    for name in names:
        if not name or len(name.strip()) < 2:
            continue
        parts = _name_parts(name)
        if not parts:
            continue
        full = gap.join(_accent_forms(part) for part in parts)
        patterns.append(re.compile(full, re.IGNORECASE))
        if len(parts) >= 2 and len(parts[-1]) >= 2:
            surname = rf"(?<![{_NAME_CHAR}])(?i:{_accent_forms(parts[-1])})(?![{_NAME_CHAR}@])"
            patterns.append(re.compile(surname))
        if len(parts) >= 2 and len(parts[0]) >= 2:
            given = rf"(?<![{_NAME_CHAR}])(?i:{_accent_forms(parts[0])})(?![{_NAME_CHAR}@])"
            patterns.append(re.compile(given))
        if len(parts) == 2:
            reversed_name = (
                rf"(?<![{_NAME_CHAR}])(?i:"
                + _accent_forms(parts[1])
                + r"[\s\u00a0]*,[\s\u00a0]*"
                + _accent_forms(parts[0])
                + rf")(?![{_NAME_CHAR}@])"
            )
            patterns.append(re.compile(reversed_name))
    return patterns


def _find_all(pattern: re.Pattern[str], text: str, resume_after: str | None = None) -> Iterator[tuple[int, int]]:
    """Yield the span of every match of ``pattern``, overlapping matches included.

    ``finditer`` resumes after each match. A phone match that starts on the number
    before a phone number ("555-123-4567 555-987-6543", "Order #1234 555-123-4567")
    ends partway through the real one, and the real one's own match is never found, so
    its last digits stayed in clear. Searching again from one character past each
    match start finds both, and ``_tokenize_values`` merges them into one value.
    ``search`` with a start position still reads the characters before it, so word
    boundaries and lookbehinds behave as on the whole text.

    With ``resume_after``, the next search starts one past that character in the match
    instead. Every email match starting inside an earlier one's local part ends at the
    same "@" and domain, and re-searching from each of those positions made a long
    email-shaped run ("a-a-...-a@b.com") quadratic.
    """
    position = 0
    while (match := pattern.search(text, position)) is not None:
        yield match.span()
        anchor = text.find(resume_after, match.start(), match.end()) if resume_after else -1
        position = anchor + 1 if anchor >= 0 else match.start() + 1


class _RestoredText:
    """``text`` with each token the store issued replaced by its original.

    Detection reads ``restored``; ``text_span`` maps a span of it back to ``text``,
    widened to whole tokens. Lookups use binary search, so mapping every value stays
    fast when the agent's reply holds many tokens.
    """

    def __init__(self, text: str, issued: list[tuple[int, int]], store: PiiTokenStore) -> None:
        parts: list[str] = []
        # Per token: its original's span in ``restored`` and the token's span in ``text``.
        self._pieces: list[tuple[int, int, int, int]] = []
        # Text index minus restored index just after each token's original.
        self._shifts: list[int] = []
        cursor = length = 0
        for start, end in issued:
            original = store.mappings[text[start:end]].original
            parts += [text[cursor:start], original]
            length += start - cursor
            self._pieces.append((length, length + len(original), start, end))
            length += len(original)
            self._shifts.append(end - length)
            cursor = end
        parts.append(text[cursor:])
        self.restored = "".join(parts)
        self._starts = [piece[0] for piece in self._pieces]

    def text_span(self, start: int, end: int) -> tuple[int, int]:
        """Map ``restored[start:end]`` back to ``text``, widened to whole tokens."""
        index = bisect_right(self._starts, start) - 1
        if index >= 0 and start < self._pieces[index][1]:
            text_start = self._pieces[index][2]
        else:
            text_start = start + (self._shifts[index] if index >= 0 else 0)
        index = bisect_left(self._starts, end) - 1
        if index >= 0 and end <= self._pieces[index][1]:
            text_end = self._pieces[index][3]
        else:
            text_end = end + (self._shifts[index] if index >= 0 else 0)
        return text_start, text_end


def _tokenize_values(
    text: str,
    issued: list[tuple[int, int]],
    candidates: list[_Candidate],
    store: PiiTokenStore,
) -> str:
    """Replace every detected value in ``text`` with its token, in one pass.

    ``candidates`` are the values every detector found, in detection order, already
    mapped to ``text`` and widened to whole issued tokens (``issued``). The detectors
    used to run one after another over the growing result, so a later one read an
    earlier one's tokens: an all-digit token id read as a phone number and nested (ADO
    build 25), and an email ending in the customer's first name stopped partway
    through the name token and reached the AI raw (Codex review of #210).

    Values that overlap are one value: the union of their spans gets one token, typed
    after its longest member (the first detected on a tie). So an email holding the
    customer's name reaches the AI as one email token, with no part of it left in
    clear (SPEC-1543). Each issued token inside a value has its original put back
    before the value is tokenized. A value found inside one token's original covers
    just that token, so it gets the same token back (``PiiTokenStore.add``
    deduplicates by original).
    """
    # Sort by position, then merge overlapping values. Each group keeps its end and its
    # best member as values join, so a name that overlaps itself many times ("aa" in a
    # long run of "a") stays linear (Codex review of #210).
    ordered = sorted((start, end, order, length, kind) for order, (start, end, length, kind) in enumerate(candidates))
    groups: list[tuple[int, int, tuple[int, int, str]]] = []  # start, end, (-length, order, type)
    for start, end, order, length, kind in ordered:
        rank = (-length, order, kind)
        if groups and start < groups[-1][1]:
            group_start, group_end, best = groups[-1]
            groups[-1] = (group_start, max(group_end, end), min(best, rank))
        else:
            groups.append((start, end, rank))

    pieces, position, next_token = [], 0, 0
    for start, end, (_, _, entity_type) in groups:
        # Groups and issued tokens are both in text order, and every token is either
        # wholly inside a group or outside all of them, so one pass covers both.
        while next_token < len(issued) and issued[next_token][1] <= start:
            next_token += 1
        original, cursor = "", start
        while next_token < len(issued) and issued[next_token][1] <= end:
            token_start, token_end = issued[next_token]
            original += text[cursor:token_start] + store.mappings[text[token_start:token_end]].original
            cursor = token_end
            next_token += 1
        pieces += [text[position:start], store.add(original + text[cursor:end], entity_type)]
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


class PiiTokenizer:
    """Reversible PII tokenizer for the transport layer.

    Usage::

        tokenizer = PiiTokenizer()

        # Before agent dispatch (SPEC-1543)
        safe_text = tokenizer.tokenize(
            customer_message,
            conversation_id="conv-123",
            tenant_id="tenant-456",
        )

        # After Critic validation (SPEC-1544)
        response = tokenizer.detokenize(
            ai_response,
            conversation_id="conv-123",
            tenant_id="tenant-456",
        )
    """

    def __init__(
        self,
        customer_names: list[str] | None = None,
    ) -> None:
        """Initialize tokenizer with optional customer context.

        Args:
            customer_names: Known customer names from profile context.
                These are tokenized as entity_type="name" since name
                detection cannot rely on regex alone.
        """
        self._customer_names = customer_names or []

    def _get_store(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> PiiTokenStore:
        """Get or create the token store for a conversation."""
        key = f"{tenant_id}:{conversation_id}"
        if key not in _conversation_stores:
            _conversation_stores[key] = PiiTokenStore(
                conversation_id=conversation_id,
                tenant_id=tenant_id,
            )
        return _conversation_stores[key]

    def tokenize(
        self,
        text: str,
        conversation_id: str,
        tenant_id: str,
    ) -> str:
        """Replace PII entities with reversible tokens (SPEC-1543).

        Every detector (customer names, matched case-insensitively anywhere in the
        text, then emails, phones and order numbers) reads the text with this
        conversation's tokens restored, so a value split by a token the agent's reply
        echoes is still found. Overlapping values become one token typed after the
        longest; detection order only breaks ties. Each unique PII value gets a stable
        token within the conversation.

        Args:
            text: Input text containing potential PII.
            conversation_id: Conversation scope.
            tenant_id: Tenant scope.

        Returns:
            Text with PII replaced by ``pii-{type}-{uuid}`` tokens.
        """
        if not text:
            return text

        store = self._get_store(conversation_id, tenant_id)

        # The agent's reply echoes this conversation's tokens. Detect on the text with
        # them restored, so "pii-name-... Smith" is read as the full name, then map
        # each value back and widen it to whole tokens.
        issued = [m.span() for m in _TOKEN_RE.finditer(text) if m.group() in store.mappings]
        restored = _RestoredText(text, issued, store)
        detectors = [("name", pattern, None) for pattern in _customer_name_patterns(self._customer_names)]
        candidates: list[_Candidate] = [
            (*restored.text_span(start, end), end - start, entity_type)
            for entity_type, pattern, resume_after in [*detectors, *_DETECTORS]
            for start, end in (
                _find_email_spans(restored.restored)
                if entity_type == "email"
                else _find_all(pattern, restored.restored, resume_after)
            )
        ]
        result = _tokenize_values(text, issued, candidates, store)

        if store.mappings:
            logger.debug(
                "PII tokenized: conv=%s entities=%d types=%s",
                conversation_id,
                len(store.mappings),
                list({m.entity_type for m in store.mappings.values()}),
            )

        return result

    def detokenize(
        self,
        text: str,
        conversation_id: str,
        tenant_id: str,
    ) -> str:
        """Restore original PII values from tokens (SPEC-1544).

        Args:
            text: Text containing ``pii-{type}-{uuid}`` tokens.
            conversation_id: Conversation scope.
            tenant_id: Tenant scope.

        Returns:
            Text with tokens replaced by original PII values.
        """
        if not text:
            return text

        store = self._get_store(conversation_id, tenant_id)
        result = text

        # One pass over the tokens in the text. Replacing each token in turn over the
        # growing result rescanned values already restored, so an original that held
        # another token's text was rewritten as well.
        result = _TOKEN_RE.sub(lambda match: store.resolve(match.group()) or match.group(), result)

        return result

    def get_mappings(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """Get all token mappings for Cosmos DB persistence (SPEC-1545)."""
        store = self._get_store(conversation_id, tenant_id)
        return store.to_cosmos_docs()

    @staticmethod
    def clear_conversation(conversation_id: str, tenant_id: str) -> None:
        """Clear cached mappings for a completed conversation."""
        key = f"{tenant_id}:{conversation_id}"
        _conversation_stores.pop(key, None)

    @staticmethod
    def clear_tenant(tenant_id: str) -> None:
        """Clear all cached mappings for a tenant (GDPR deletion)."""
        keys_to_remove = [
            k for k in _conversation_stores if k.startswith(f"{tenant_id}:")
        ]
        for key in keys_to_remove:
            del _conversation_stores[key]
