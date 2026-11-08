# tests/unit/test_pii_tokenizer.py: a token the tokenizer issued is never read as PII
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
PiiTokenizer issues tokens as ``pii-{type}-{12 hex characters}``. Its detection passes
(names, emails, phones, order numbers) used to run one after another over the growing
result, so a later pass read an earlier pass's tokens:

- A token whose 12 hex characters are all digits read as a phone number (about 1 token
  in 280), and a short customer name such as "Ed" can occur in a token's hex. The
  tokens nested, and ``detokenize`` left the outer token in the customer's reply. ADO
  build 25 failed tests/unit/test_peer_agent_critic.py this way: the customer got
  ``pii-email-836890970487`` back instead of their email address.
- An email ending in the customer's first name (``alice@example.jo`` for "Jo") stopped
  partway through the name token, so the mailbox and domain reached the AI raw (Codex
  review of #210).

Every detector now reads the original text, and overlapping values become one token,
so no part of an address that holds the customer's name reaches the AI (SPEC-1543).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from src.multi_tenant import pii_tokenizer
from src.multi_tenant.pii_tokenizer import PiiTokenizer

TENANT = "t-pii"
CONVERSATION = "conv-pii"


@pytest.fixture(autouse=True)
def _fresh_store() -> Iterator[None]:
    """The token stores are module-level, so each test starts and ends with an empty one."""
    PiiTokenizer.clear_conversation(CONVERSATION, TENANT)
    yield
    PiiTokenizer.clear_conversation(CONVERSATION, TENANT)


def _issue_token_ids(monkeypatch: pytest.MonkeyPatch, *hex_ids: str) -> None:
    """Make the tokenizer issue these 12-character token ids, in order."""
    ids = iter(uuid.UUID(hex_id.ljust(32, "0")) for hex_id in hex_ids)
    monkeypatch.setattr(pii_tokenizer, "uuid", SimpleNamespace(uuid4=lambda: next(ids)))


def _round_trip(tokenizer: PiiTokenizer, text: str) -> tuple[str, str]:
    tokenized = tokenizer.tokenize(text, conversation_id=CONVERSATION, tenant_id=TENANT)
    return tokenized, tokenizer.detokenize(tokenized, CONVERSATION, TENANT)


def _originals(tokenizer: PiiTokenizer) -> list[str]:
    return [doc["original"] for doc in tokenizer.get_mappings(CONVERSATION, TENANT)]


def test_an_all_digit_email_token_is_not_read_as_a_phone_number(monkeypatch):
    """The token from ADO build 25, with a second id ready in case the phone pass takes one."""
    _issue_token_ids(monkeypatch, "836890970487", "abcdefabcdef")
    tokenizer = PiiTokenizer()
    text = "I've sent the refund details to jo.customer@example.com."
    tokenized, restored = _round_trip(tokenizer, text)
    assert tokenized == "I've sent the refund details to pii-email-836890970487."
    assert restored == text
    assert _originals(tokenizer) == ["jo.customer@example.com"]


def test_a_short_customer_name_is_not_found_inside_an_earlier_name_token(monkeypatch):
    """The pipeline passes the full name and then the first name; "Ed" can occur in a token's hex."""
    _issue_token_ids(monkeypatch, "ed1234567890", "abcdefabcdef")
    tokenizer = PiiTokenizer(customer_names=["Ed Jones", "Ed"])
    text = "Hi, this is Ed Jones."
    tokenized, restored = _round_trip(tokenizer, text)
    assert tokenized == "Hi, this is pii-name-ed1234567890."
    assert restored == text
    assert _originals(tokenizer) == ["Ed Jones"]


def test_text_that_only_looks_like_a_token_is_still_scanned(monkeypatch):
    """Only tokens this conversation's store issued are skipped; a typed look-alike can hold a real number."""
    _issue_token_ids(monkeypatch, "abcdefabcdef")
    tokenizer = PiiTokenizer()
    text = "My reference is pii-email-555123456789."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "555123456789" not in tokenized
    assert restored == text


def test_every_value_is_tokenized_in_place_and_repeats_share_a_token(monkeypatch):
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2", "ccccccccccc3", "ddddddddddd4")
    tokenizer = PiiTokenizer(customer_names=["Jo Customer"])
    text = "Jo Customer, jo@example.com: call 555-123-4567 about #123456, or write to jo@example.com."
    tokenized, restored = _round_trip(tokenizer, text)
    for value in ("Jo Customer", "jo@example.com", "555-123-4567", "#123456"):
        assert value not in tokenized
    assert restored == text
    assert sorted(_originals(tokenizer)) == sorted(["Jo Customer", "jo@example.com", "555-123-4567", "#123456"])


@pytest.mark.parametrize(
    ("names", "text", "tokenized_text"),
    [
        (["John Smith", "John"], "Please write to smith.john@gmail.com", "Please write to pii-email-aaaaaaaaaaa1"),
        (["Jo Smith", "Jo"], "Please write to smith.jo@example.com.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["Ann Lee", "Ann"], "Please write to joann.lee@gmail.com.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["Ed Jones", "Ed"], "Please write to fred.jones@example.com.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["John Smith", "John"], "Please write to info+john@shop.example.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["John Smith", "John"], "Please write to john.smith@gmail.com.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["Jo"], "Please write to alice@example.jo.", "Please write to pii-email-aaaaaaaaaaa1."),
        (["Jo Smith", "Jo"], "Please write to alice@smith.jo", "Please write to pii-email-aaaaaaaaaaa1"),
        (["Mary Ann"], "Please write to mary ann@shop.example.", "Please write to pii-email-aaaaaaaaaaa1."),
        (
            ["John Smith", "John"],
            "John, please write to smith.john@gmail.com.",
            "pii-name-aaaaaaaaaaa1, please write to pii-email-bbbbbbbbbbb2.",
        ),
    ],
    ids=[
        "name-ends-address",
        "short-name-ends-address",
        "name-mid-word",
        "short-name-mid-word",
        "name-after-plus",
        "name-starts-address",
        "name-ends-domain",
        "surname-in-domain-name-ends-domain",
        "name-crosses-address-start",
        "name-also-outside-address",
    ],
)
def test_an_email_that_holds_the_customer_name_reaches_the_ai_as_one_email_token(
    monkeypatch, names, text, tokenized_text
):
    """A name inside, or partly inside, an address is part of the one email token, not a token of its own."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2", "ccccccccccc3")
    tokenizer = PiiTokenizer(customer_names=names)
    tokenized, restored = _round_trip(tokenizer, text)
    for fragment in ("smith", "jones", "alice", "example", "shop", "gmail", "mary"):
        assert fragment not in tokenized.lower()
    assert tokenized == tokenized_text
    assert restored == text
    assert len(_originals(tokenizer)) == tokenized_text.count("pii-")


def test_a_token_the_reply_echoes_is_kept_and_the_rest_is_still_tokenized(monkeypatch):
    """The Critic path tokenizes the agent's reply, which already holds this conversation's tokens."""
    _issue_token_ids(monkeypatch, "836890970487", "abcdefabcdef")
    tokenizer = PiiTokenizer()
    email_token, _ = _round_trip(tokenizer, "jo.customer@example.com")
    assert email_token == "pii-email-836890970487"
    tokenized, restored = _round_trip(tokenizer, f"Sent to {email_token}; call 555-123-4567.")
    assert tokenized == "Sent to pii-email-836890970487; call pii-phone-abcdefabcdef."
    assert restored == "Sent to jo.customer@example.com; call 555-123-4567."


def test_a_value_that_crosses_an_echoed_token_is_tokenized_whole(monkeypatch):
    """A reply holding ``alice@example.`` and then a name token must not send ``alice@example.`` to the AI."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer(customer_names=["Jo"])
    name_token, _ = _round_trip(tokenizer, "Jo")
    assert name_token == "pii-name-aaaaaaaaaaa1"
    tokenized, restored = _round_trip(tokenizer, f"Write to alice@example.{name_token}.")
    assert tokenized == "Write to pii-email-bbbbbbbbbbb2."
    assert restored == "Write to alice@example.Jo."


def test_a_long_order_number_is_one_order_token(monkeypatch):
    """Eight digits also read as a phone number; the order number holds them, so it is one order token."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer()
    tokenized, restored = _round_trip(tokenizer, "Where is order #12345678?")
    assert tokenized == "Where is order pii-order_number-aaaaaaaaaaa1?"
    assert restored == "Where is order #12345678?"
    assert _originals(tokenizer) == ["#12345678"]


def _clear_text(tokenized: str) -> str:
    """What the AI reads outside the tokens."""
    return pii_tokenizer._TOKEN_RE.sub("", tokenized)


@pytest.mark.parametrize(
    ("names", "text", "secrets"),
    [
        ([], "My numbers are 555-123-4567 555-987-6543", ("4567", "987", "6543")),
        ([], "Order #1234\n555-123-4567", ("123", "4567")),
        ([], "since 2024 555-123-4567", ("123", "4567")),
        ([], "call 555-123-4567 or 555-123-4568 today", ("4567", "4568")),
        (["Ana"], "Hi, Anana here", ("na",)),
    ],
    ids=["phone-after-phone", "phone-after-order", "phone-after-year", "phone-pair-or", "name-repeats-into-itself"],
)
def test_a_value_after_an_overlapping_match_is_not_left_partly_in_clear(names, text, secrets):
    """finditer resumes after each match, so a phone match starting on the number before
    hid the real number's match and its last digits reached the AI (review of 9ee0cf45)."""
    tokenizer = PiiTokenizer(customer_names=names)
    tokenized, restored = _round_trip(tokenizer, text)
    assert restored == text
    clear = _clear_text(tokenized)
    for secret in secrets:
        assert secret not in clear, f"{secret!r} reaches the AI in {tokenized!r}"


def test_a_name_split_by_an_echoed_token_is_tokenized_whole(monkeypatch):
    """The Critic path tokenizes the agent's reply. "pii-name-... Smith" reads as the full name,
    so "Smith" must not reach the Critic in clear."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer(customer_names=["John Smith", "John"])
    first_name_token, _ = _round_trip(tokenizer, "John")
    assert first_name_token == "pii-name-aaaaaaaaaaa1"
    tokenized, restored = _round_trip(tokenizer, f"Dear {first_name_token} Smith, ok")
    assert tokenized == "Dear pii-name-bbbbbbbbbbb2, ok"
    assert restored == "Dear John Smith, ok"


def test_an_echoed_token_whose_original_is_detected_again_keeps_its_token(monkeypatch):
    """Detection reads the reply with tokens restored; a value that is one token's original,
    or lies inside it, gets that same token back rather than a new one."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer(customer_names=["Jo"])
    email_token, _ = _round_trip(tokenizer, "jo.customer@example.com")
    assert email_token == "pii-email-aaaaaaaaaaa1"
    tokenized, restored = _round_trip(tokenizer, f"Mail {email_token} and {email_token}.")
    assert tokenized == f"Mail {email_token} and {email_token}."
    assert restored == "Mail jo.customer@example.com and jo.customer@example.com."
    assert _originals(tokenizer) == ["jo.customer@example.com"]


def test_a_name_that_overlaps_itself_across_a_long_message_is_one_token(monkeypatch):
    """Codex review of #210: "aa" in a long run of "a" matches at every position; the values
    merge into one token, and grouping keeps a running end instead of rescanning."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["aa"])
    text = "a" * 4000
    tokenized, restored = _round_trip(tokenizer, text)
    assert tokenized == "pii-name-aaaaaaaaaaa1"
    assert restored == text


def test_detokenize_restores_each_token_once(monkeypatch):
    """detokenize replaced each token in turn over the growing result, so an original holding
    another token's text was rewritten too (second adversarial review of #210)."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer(customer_names=["Jo"])
    typed = "mail pii-name-bbbbbbbbbbb2@shop.io"
    tokenized = tokenizer.tokenize(typed, CONVERSATION, TENANT)
    assert tokenized == "mail pii-email-aaaaaaaaaaa1"
    assert tokenizer.tokenize("Jo", CONVERSATION, TENANT) == "pii-name-bbbbbbbbbbb2"
    assert tokenizer.detokenize(tokenized, CONVERSATION, TENANT) == typed


def test_a_token_id_collision_never_overwrites_a_mapping(monkeypatch):
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "aaaaaaaaaaa1", "bbbbbbbbbbb2")
    tokenizer = PiiTokenizer()
    first, _ = _round_trip(tokenizer, "a@example.com")
    second, _ = _round_trip(tokenizer, "b@example.com")
    assert (first, second) == ("pii-email-aaaaaaaaaaa1", "pii-email-bbbbbbbbbbb2")
    assert tokenizer.detokenize(f"{first} {second}", CONVERSATION, TENANT) == "a@example.com b@example.com"


@pytest.mark.parametrize(
    "text",
    ["a-" * 20000 + "a@b.com", "a." * 20000 + "a@b.com", "111-" * 10000 + "1@b.com", "555-123-4567 " * 3000],
    ids=["dashed-local-part", "dotted-local-part", "digit-local-part", "phone-run"],
)
def test_long_overlapping_runs_tokenize_in_linear_time(text):
    """The agent's reply has no length cap. Overlapping matches merge into one value, and email
    searches resume after the "@", so a 40,000-character run stays fast (it took seconds before)."""
    tokenizer = PiiTokenizer()
    started = time.perf_counter()
    tokenized, restored = _round_trip(tokenizer, text)
    assert time.perf_counter() - started < 5
    assert restored == text
    assert _clear_text(tokenized).strip() == ""


def test_a_long_accented_run_without_an_at_sign_stays_fast():
    """S23 review. A run of accented letters and no @ must not make email search quadratic."""
    tokenizer = PiiTokenizer()
    text = "\u00e9" * 40000
    started = time.perf_counter()
    tokenized, restored = _round_trip(tokenizer, text)
    assert time.perf_counter() - started < 0.5
    assert tokenized == text
    assert restored == text


@pytest.mark.parametrize("prefix", ["\u0130", "\u0131", "\u017f", "\u212a"])
def test_an_order_number_after_a_casefold_letter_is_not_left_in_clear(prefix, monkeypatch):
    """S23 review. IGNORECASE must not treat these letters as ASCII word characters."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = f"{prefix}ORD-1234567"
    tokenized, restored = _round_trip(tokenizer, text)
    assert "ORD-1234567" not in tokenized
    assert restored == text


def test_an_accented_email_is_not_left_in_clear(monkeypatch):
    """S23. An accented letter is part of the address. It is not a word boundary that leaves the start in clear."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = "Write to jos\u00e9.garcia@gmail.com today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "jos\u00e9" not in tokenized
    assert "pii-email-" in tokenized
    assert restored == text
    assert _originals(tokenizer) == ["jos\u00e9.garcia@gmail.com"]


def test_values_written_against_cjk_text_are_not_left_in_clear(monkeypatch):
    """S23. A CJK character is not a word boundary that hides a phone, an order number, or an email."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1", "bbbbbbbbbbb2", "ccccccccccc3")
    tokenizer = PiiTokenizer()
    text = "\u6211\u7684\u7535\u8bdd\u662f555-123-4567\u8c22\u8c22 \u8ba2\u5355ORD-1234567\u8c22 \u90ae\u7bb1user@example.com\u8c22"
    tokenized, restored = _round_trip(tokenizer, text)
    assert "555-123-4567" not in tokenized
    assert "ORD-1234567" not in tokenized
    assert "user@example.com" not in tokenized
    assert restored == text


def test_a_given_name_alone_is_not_left_in_clear(monkeypatch):
    """Decision 24. The stored given name matches alone. A longer word does not."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["Ada Lovelace"])
    tokenized, restored = _round_trip(tokenizer, "Thanks, Ada")
    assert "Ada" not in tokenized
    assert restored == "Thanks, Ada"
    other = PiiTokenizer(customer_names=["Ada Lovelace"])
    unchanged, _restored_other = _round_trip(other, "Adam")
    assert unchanged == "Adam"


def test_a_surname_alone_is_not_left_in_clear(monkeypatch):
    """Decision 12. The stored surname is tokenized when the message uses it alone."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["John Smith"])
    text = "Please help Smith with an order."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "Smith" not in tokenized
    assert "pii-name-" in tokenized
    assert restored == text


def test_a_reversed_surname_comma_name_is_not_left_in_clear(monkeypatch):
    """Decision 12. 'Smith, John' is the stored name 'John Smith'."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["John Smith"])
    text = "Please help Smith, John with an order."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "Smith, John" not in tokenized
    assert "Smith" not in _clear_text(tokenized)
    assert "John" not in _clear_text(tokenized)
    assert restored == text


def test_a_name_with_a_nonbreaking_space_is_not_left_in_clear(monkeypatch):
    """Decision 12. A non-breaking space between the parts is still the stored name."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["John Smith"])
    text = "Please help John\u00a0Smith with an order."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "John" not in tokenized
    assert "Smith" not in tokenized
    assert restored == text


def test_a_decomposed_accented_name_is_not_left_in_clear(monkeypatch):
    """Decision 12. A stored composed name matches the same name written decomposed."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer(customer_names=["Jos\u00e9 Garc\u00eda"])
    text = "Please help Jose\u0301 Garc\u0069\u0301a with an order."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "\u0301" not in tokenized
    assert "pii-name-" in tokenized
    assert restored == text


def test_a_multi_segment_order_number_is_not_left_in_clear(monkeypatch):
    """Decision 12. INV-2024-00123 is one order number, not a year left in clear."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = "Where is INV-2024-00123?"
    tokenized, restored = _round_trip(tokenizer, text)
    assert "00123" not in _clear_text(tokenized)
    assert "2024" not in _clear_text(tokenized)
    assert restored == text
    assert _originals(tokenizer) == ["INV-2024-00123"]


def test_a_greek_email_local_part_is_not_left_in_clear(monkeypatch):
    """Decision 18. Greek letters before the @ are part of the address."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = "Write to \u03b5\u03bb\u03ad\u03bd\u03b7@example.com today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "\u03b5\u03bb\u03ad\u03bd\u03b7" not in tokenized
    assert "pii-email-" in tokenized
    assert restored == text
    assert _originals(tokenizer) == ["\u03b5\u03bb\u03ad\u03bd\u03b7@example.com"]


def test_a_cyrillic_email_local_part_is_not_left_in_clear(monkeypatch):
    """Decision 18. Cyrillic letters before the @ are part of the address."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = "Write to \u0438\u0432\u0430\u043d@example.com today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "\u0438\u0432\u0430\u043d" not in tokenized
    assert "pii-email-" in tokenized
    assert restored == text
    assert _originals(tokenizer) == ["\u0438\u0432\u0430\u043d@example.com"]


@pytest.mark.parametrize(
    "address",
    ["kontakt@b\u00fccher.de", "info@\u043f\u0440\u0438\u043c\u0435\u0440.\u0440\u0444"],
    ids=["latin-domain", "cyrillic-domain"],
)
def test_an_internationalized_domain_is_not_left_in_clear(address, monkeypatch):
    """Decision 18. A Unicode domain is the address, in one email token."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    text = f"Write to {address} today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert address not in tokenized
    assert "pii-email-" in tokenized
    assert restored == text
    assert _originals(tokenizer) == [address]


def test_a_punycode_domain_is_not_left_in_clear(monkeypatch):
    """Decision 18. The punycode form of an internationalized domain stays detected."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    address = "kontakt@xn--bcher-kva.de"
    text = f"Write to {address} today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert address not in tokenized
    assert "pii-email-" in tokenized
    assert restored == text
    assert _originals(tokenizer) == [address]


def test_a_mixed_accent_spelling_is_not_left_in_clear(monkeypatch):
    """A stored part that mixes composed and decomposed accents still matches itself."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    stored = "Mar\u00eda-Jose\u0301 Smith"
    tokenizer = PiiTokenizer(customer_names=[stored])
    text = f"Please help {stored} with an order."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "Mar\u00eda-Jose\u0301" not in tokenized
    assert "Smith" not in tokenized
    assert restored == text


def test_a_decomposed_email_is_not_left_in_clear(monkeypatch):
    """A combining mark is part of the address, not a place where the mailbox stays in clear."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    address = "jose\u0301.garcia@gmail.com"
    text = f"Write to {address} today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert "jose" not in tokenized
    assert "\u0301" not in tokenized
    assert restored == text
    assert _originals(tokenizer) == [address]


def test_a_decomposed_domain_is_not_left_in_clear(monkeypatch):
    """A combining mark in the domain is still the address."""
    _issue_token_ids(monkeypatch, "aaaaaaaaaaa1")
    tokenizer = PiiTokenizer()
    address = "kontakt@bu\u0308cher.de"
    text = f"Write to {address} today."
    tokenized, restored = _round_trip(tokenizer, text)
    assert address not in tokenized
    assert "\u0308" not in tokenized
    assert restored == text
    assert _originals(tokenizer) == [address]
