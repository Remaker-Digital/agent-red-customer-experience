# tests/unit/test_single_use_token_consumption.py: one winner per single-use token
# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

"""
Review S13 (docs/agent-red-architecture-review.md): single-use tokens could be
redeemed twice. Each consumption read the document, checked it was unused,
then wrote "used" with no condition, so two concurrent requests could both
pass the check and both be let in. This covered verification, sign-in and
recovery tokens (``consume_token``), sign-in codes (``consume_token_by_code``)
and MFA backup codes (the admin 2FA endpoint and ``MfaTotpService``).

Each consumption is now a conditional write carrying the read document's
ETag, so Cosmos applies exactly one of two concurrent writes and rejects the
other with HTTP 412, which is treated as "already used". The widget and
in-conversation OTP checks accept a correct code only when their
``consume_token`` call wins.

The container double below enforces ETags the way Cosmos does, and can hold
concurrent callers at a barrier after their reads so that both read before
either writes. On the code before this change, the concurrency and stale
ETag tests let two callers in.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from azure.core import MatchConditions
from azure.cosmos.exceptions import (
    CosmosAccessConditionFailedError,
    CosmosHttpResponseError,
    CosmosResourceNotFoundError,
)

from src.chat.identity_preprocessor import (
    _verify_otp_for_conversation,
    _verify_sms_otp_for_conversation,
)
from src.multi_tenant.admin_mfa_auth import (
    BackupVerifyRequest,
    _failed_attempts,
    configure_2fa_services,
    create_pending_2fa_token,
    verify_backup_2fa,
)
from src.multi_tenant.mfa_totp import MfaTotpService, hash_backup_code
from src.multi_tenant.repositories.team import TeamMemberRepository
from src.multi_tenant.repositories.verification import VerificationTokenRepository
from src.multi_tenant.sms_verification import hash_code
from src.multi_tenant.widget_otp_verification import (
    _OTP_TOKEN_TYPE,
    _SMS_OTP_TOKEN_TYPE,
    OtpVerifyRequest,
    SmsVerifyRequest,
)
from src.multi_tenant.widget_otp_verification import verify_otp as widget_verify_otp
from src.multi_tenant.widget_otp_verification import verify_sms_otp as widget_verify_sms_otp

TOKEN_TYPE = "magic_link"
TOKEN_ID = "Zt8kQ2vN5xR7pL0mW3yB6cD9fH1jK4sA"
SIGN_IN_CODE = "482913"
TENANT_ID = "tenant-s13"
MEMBER_ID = "member-admin"
ADMIN_EMAIL = "admin@example.com"
BACKUP_CODES = ["ABCD2345", "EFGH6789"]
VERIFICATION_LOGGER = "src.multi_tenant.repositories.verification"


class EtagContainer:
    """Cosmos container double that enforces ETags as Cosmos does.

    Every write gives the stored document a new ``_etag``. A patch sent with
    ``match_condition=MatchConditions.IfNotModified`` and an ETag that is no
    longer current fails with CosmosAccessConditionFailedError (HTTP 412).
    The SDK's own argument checks are mirrored. Reads and queries return
    copies; ``after_read`` runs once a caller has its copy, and
    ``read_barrier`` holds callers until all of them have read.
    """

    def __init__(self, partition_field: str) -> None:
        self._partition_field = partition_field
        self._docs: dict[str, dict[str, Any]] = {}
        self._version = 0
        self.read_barrier: asyncio.Barrier | None = None
        self.after_read: Callable[[str], None] | None = None
        self.patch_calls: list[dict[str, Any]] = []
        self.fail_patches_with: Exception | None = None

    def _next_etag(self) -> str:
        self._version += 1
        return f'"etag-{self._version}"'

    def put(self, doc: dict[str, Any], *, with_etag: bool = True) -> None:
        stored = copy.deepcopy(doc)
        if with_etag:
            stored["_etag"] = self._next_etag()
        self._docs[stored["id"]] = stored

    def stored(self, item_id: str) -> dict[str, Any]:
        return self._docs[item_id]

    def touch(self, item_id: str) -> None:
        """Another writer changes the document, and so its ETag."""
        self._docs[item_id]["_etag"] = self._next_etag()

    def _find(self, item: str, partition_key: str) -> dict[str, Any]:
        doc = self._docs.get(item)
        if doc is None or doc.get(self._partition_field) != partition_key:
            raise CosmosResourceNotFoundError(status_code=404, message="Entity not found")
        return doc

    async def _hand_over(self, doc: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(doc)
        if self.after_read is not None:
            self.after_read(doc["id"])
        if self.read_barrier is not None:
            await self.read_barrier.wait()
        return snapshot

    async def read_item(self, item: str, partition_key: str, **_: Any) -> dict[str, Any]:
        return await self._hand_over(self._find(item, partition_key))

    def query_items(self, query: str, parameters: list[dict[str, Any]], partition_key: str, **_: Any):
        """Only the sign-in code query: tenant, code and unused."""
        fields = {"@tid": "tenant_id", "@code": "sign_in_code"}
        wanted = {fields[p["name"]]: p["value"] for p in parameters}
        assert "c.used = false" in query

        async def results():
            for doc in list(self._docs.values()):
                if doc.get(self._partition_field) != partition_key or doc.get("used") is not False:
                    continue
                if all(doc.get(field) == value for field, value in wanted.items()):
                    yield await self._hand_over(doc)

        return results()

    async def patch_item(
        self,
        item: str,
        partition_key: str,
        patch_operations: list[dict[str, Any]],
        *,
        etag: str | None = None,
        match_condition: MatchConditions | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        doc = self._find(item, partition_key)
        self.patch_calls.append({"item": item, "etag": etag, "match_condition": match_condition})
        if self.fail_patches_with is not None:
            raise self.fail_patches_with
        self._check_precondition(doc, etag, match_condition)
        for op in patch_operations:
            assert op["op"] in ("set", "replace"), op
            doc[op["path"].lstrip("/")] = copy.deepcopy(op["value"])
        doc["_etag"] = self._next_etag()
        return copy.deepcopy(doc)

    @staticmethod
    def _check_precondition(
        doc: dict[str, Any],
        etag: str | None,
        match_condition: MatchConditions | None,
    ) -> None:
        # Mirrors azure.cosmos._base._get_match_headers, then the If-Match check.
        if match_condition is None:
            if etag is not None:
                raise ValueError("'etag' specified without 'match_condition'.")
            return
        assert match_condition is MatchConditions.IfNotModified, match_condition
        if not etag:
            raise ValueError("'match_condition' specified without 'etag'.")
        if doc.get("_etag") != etag:
            raise CosmosAccessConditionFailedError(status_code=412, message="Precondition failed")


def _manager_for(container: EtagContainer) -> MagicMock:
    manager = MagicMock()
    manager.get_container.return_value = container
    return manager


async def _concurrently(container: EtagContainer, *calls):
    """Run the calls together; every read completes before any write."""
    container.read_barrier = asyncio.Barrier(len(calls))
    try:
        return await asyncio.wait_for(asyncio.gather(*calls), timeout=5)
    finally:
        container.read_barrier = None


# ---------------------------------------------------------------------------
# Verification tokens: consume_token and consume_token_by_code
# ---------------------------------------------------------------------------


def _token(**overrides: Any) -> dict[str, Any]:
    return {
        "id": TOKEN_ID,
        "token_type": TOKEN_TYPE,
        "tenant_id": TENANT_ID,
        "email": ADMIN_EMAIL,
        "used": False,
        "sign_in_code": SIGN_IN_CODE,
        "ttl": 600,
        **overrides,
    }


@pytest.fixture
def tokens():
    container = EtagContainer(partition_field="token_type")
    with patch(
        "src.multi_tenant.repositories.verification.get_cosmos_manager",
        return_value=_manager_for(container),
    ):
        yield container


def _by_id(repo: VerificationTokenRepository):
    return repo.consume_token(TOKEN_ID, TOKEN_TYPE)


def _by_code(repo: VerificationTokenRepository):
    return repo.consume_token_by_code(TENANT_ID, SIGN_IN_CODE, TOKEN_TYPE)


CONSUMERS = [pytest.param(_by_id, id="consume_token"), pytest.param(_by_code, id="consume_token_by_code")]


@pytest.mark.parametrize("consume", CONSUMERS)
class TestVerificationTokenConsumption:
    async def test_two_concurrent_consumptions_yield_exactly_one_winner(self, tokens, consume):
        tokens.put(_token())
        repo = VerificationTokenRepository()

        results = await _concurrently(tokens, consume(repo), consume(repo))

        winners = [doc for doc in results if doc is not None]
        assert len(winners) == 1
        assert winners[0]["tenant_id"] == TENANT_ID
        assert tokens.stored(TOKEN_ID)["used"] is True

    async def test_token_changed_after_read_is_rejected(self, tokens, consume):
        tokens.put(_token())
        tokens.after_read = tokens.touch

        assert await consume(VerificationTokenRepository()) is None
        assert tokens.stored(TOKEN_ID)["used"] is False

    async def test_single_consumption_returns_the_token_and_marks_it_used(self, tokens, consume):
        tokens.put(_token())
        etag = tokens.stored(TOKEN_ID)["_etag"]

        doc = await consume(VerificationTokenRepository())

        assert doc is not None
        assert (doc["id"], doc["tenant_id"], doc["email"], doc["used"]) == (TOKEN_ID, TENANT_ID, ADMIN_EMAIL, False)
        assert tokens.stored(TOKEN_ID)["used"] is True
        assert tokens.patch_calls == [
            {"item": TOKEN_ID, "etag": etag, "match_condition": MatchConditions.IfNotModified},
        ]

    async def test_second_consumption_returns_none(self, tokens, consume):
        tokens.put(_token())
        repo = VerificationTokenRepository()

        assert await consume(repo) is not None
        assert await consume(repo) is None

    async def test_already_used_token_returns_none_without_writing(self, tokens, consume):
        tokens.put(_token(used=True))

        assert await consume(VerificationTokenRepository()) is None
        assert tokens.patch_calls == []

    async def test_token_without_etag_is_not_consumed(self, tokens, consume):
        """Cosmos returns _etag on every read; without one no conditional write is possible."""
        tokens.put(_token(), with_etag=False)

        assert await consume(VerificationTokenRepository()) is None
        assert tokens.patch_calls == []
        assert tokens.stored(TOKEN_ID)["used"] is False

    async def test_lost_race_is_logged_without_the_token(self, tokens, consume, caplog):
        tokens.put(_token())
        tokens.after_read = tokens.touch

        with caplog.at_level(logging.DEBUG, logger=VERIFICATION_LOGGER):
            assert await consume(VerificationTokenRepository()) is None

        assert "already" in caplog.text
        assert TOKEN_ID not in caplog.text
        assert SIGN_IN_CODE not in caplog.text

    async def test_other_write_failure_returns_none_and_logs_without_the_token(self, tokens, consume, caplog):
        tokens.put(_token())
        tokens.fail_patches_with = CosmosHttpResponseError(status_code=503, message="unavailable")

        with caplog.at_level(logging.DEBUG, logger=VERIFICATION_LOGGER):
            assert await consume(VerificationTokenRepository()) is None

        assert "Failed to mark token as used" in caplog.text
        assert TOKEN_ID not in caplog.text
        assert SIGN_IN_CODE not in caplog.text


# ---------------------------------------------------------------------------
# MFA backup codes
# ---------------------------------------------------------------------------


def _member() -> dict[str, Any]:
    return {
        "id": MEMBER_ID,
        "tenant_id": TENANT_ID,
        "email": ADMIN_EMAIL,
        "role": "admin",
        "mfa_enabled": True,
        "mfa_backup_code_hashes": [hash_backup_code(code) for code in BACKUP_CODES],
    }


def _hashes_left(members: EtagContainer) -> list[str]:
    return members.stored(MEMBER_ID)["mfa_backup_code_hashes"]


ALL_HASHES = [hash_backup_code(code) for code in BACKUP_CODES]
HASHES_AFTER_FIRST = [hash_backup_code(BACKUP_CODES[1])]


@pytest.fixture
def members():
    container = EtagContainer(partition_field="tenant_id")
    container.put(_member())
    _failed_attempts.clear()
    configure_2fa_services(team_repo=TeamMemberRepository())
    with (
        patch("src.multi_tenant.repositories.base.get_cosmos_manager", return_value=_manager_for(container)),
        patch("src.multi_tenant.envelope_encryption.get_envelope_encryption_service", return_value=None),
    ):
        yield container
    configure_2fa_services(None, None, None)
    _failed_attempts.clear()


async def _admin_redeem(code: str) -> tuple[int, dict[str, Any]]:
    pending_token, _ = create_pending_2fa_token(TENANT_ID, ADMIN_EMAIL, MEMBER_ID, "admin")
    response = await verify_backup_2fa(BackupVerifyRequest(pending_token=pending_token, code=code))
    return response.status_code, json.loads(response.body)


def _assert_refused_like_an_invalid_code(status: int, body: dict[str, Any]) -> None:
    assert status == 401
    assert body["error"] == "invalid_code"
    assert body["message"].startswith("Invalid backup code.")


class TestAdminBackupCodeVerify:
    """POST /api/auth/2fa/totp/backup-verify."""

    async def test_two_concurrent_redemptions_yield_exactly_one_session(self, members):
        results = await _concurrently(members, _admin_redeem(BACKUP_CODES[0]), _admin_redeem(BACKUP_CODES[0]))

        statuses = sorted(status for status, _ in results)
        assert statuses == [200, 401]
        loser = next(body for status, body in results if status == 401)
        _assert_refused_like_an_invalid_code(401, loser)
        assert _hashes_left(members) == HASHES_AFTER_FIRST

    async def test_member_changed_after_read_is_refused(self, members):
        members.after_read = members.touch

        _assert_refused_like_an_invalid_code(*await _admin_redeem(BACKUP_CODES[0]))
        assert _hashes_left(members) == ALL_HASHES

    async def test_single_redemption_issues_a_session_and_removes_the_code(self, members):
        etag = members.stored(MEMBER_ID)["_etag"]

        status, body = await _admin_redeem(BACKUP_CODES[0])

        assert status == 200
        assert body["session_token"]
        assert _hashes_left(members) == HASHES_AFTER_FIRST
        assert members.patch_calls == [
            {"item": MEMBER_ID, "etag": etag, "match_condition": MatchConditions.IfNotModified},
        ]

    async def test_used_code_is_refused(self, members):
        assert (await _admin_redeem(BACKUP_CODES[0]))[0] == 200

        _assert_refused_like_an_invalid_code(*await _admin_redeem(BACKUP_CODES[0]))
        assert _hashes_left(members) == HASHES_AFTER_FIRST

    async def test_member_without_etag_is_refused(self, members):
        members.put(_member(), with_etag=False)

        _assert_refused_like_an_invalid_code(*await _admin_redeem(BACKUP_CODES[0]))
        assert members.patch_calls == []
        assert _hashes_left(members) == ALL_HASHES


async def test_member_update_without_etag_stays_unconditional(members):
    """Other callers of update_member_fields pass no ETag and keep a plain patch."""
    await TeamMemberRepository().update_member_fields(TENANT_ID, MEMBER_ID, {"phone_verified": True})

    assert members.patch_calls == [{"item": MEMBER_ID, "etag": None, "match_condition": None}]
    assert members.stored(MEMBER_ID)["phone_verified"] is True


class TestServiceBackupVerify:
    """MfaTotpService.verify_backup (POST /mfa/backup-verify)."""

    @staticmethod
    async def _login(code: str) -> dict[str, Any] | None:
        repo = TeamMemberRepository()
        member = await repo.read(TENANT_ID, MEMBER_ID)
        return await MfaTotpService(team_repo=repo).verify_backup(member, code)

    async def test_two_concurrent_redemptions_yield_exactly_one_token(self, members):
        results = await _concurrently(members, self._login(BACKUP_CODES[0]), self._login(BACKUP_CODES[0]))

        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert winners[0]["backup_codes_remaining"] == 1
        assert _hashes_left(members) == HASHES_AFTER_FIRST

    async def test_member_changed_after_read_is_refused(self, members):
        members.after_read = members.touch

        assert await self._login(BACKUP_CODES[0]) is None
        assert _hashes_left(members) == ALL_HASHES

    async def test_single_redemption_returns_a_token_and_removes_the_code(self, members):
        etag = members.stored(MEMBER_ID)["_etag"]

        result = await self._login(BACKUP_CODES[0])

        assert result is not None
        assert result["mfa_token"]
        assert result["backup_codes_remaining"] == 1
        assert _hashes_left(members) == HASHES_AFTER_FIRST
        assert members.patch_calls == [
            {"item": MEMBER_ID, "etag": etag, "match_condition": MatchConditions.IfNotModified},
        ]

    async def test_used_code_is_refused(self, members):
        assert await self._login(BACKUP_CODES[0]) is not None

        assert await self._login(BACKUP_CODES[0]) is None
        assert _hashes_left(members) == HASHES_AFTER_FIRST

    async def test_member_without_etag_is_refused(self, members):
        members.put(_member(), with_etag=False)

        assert await self._login(BACKUP_CODES[0]) is None
        assert members.patch_calls == []
        assert _hashes_left(members) == ALL_HASHES


# ---------------------------------------------------------------------------
# OTP verification: a correct code counts only if consume_token wins
# ---------------------------------------------------------------------------
#
# The widget endpoints and the in-conversation verifier read the OTP token
# themselves, compare the code, then call consume_token. They share the token
# type and id, so a widget request can also race a chat request.

OTP_EMAIL = "customer@example.com"
OTP_PHONE = "+12125550100"
OTP_CODE = "731406"
EMAIL_OTP_ID = f"otp:{TENANT_ID}:{OTP_EMAIL}"
SMS_OTP_ID = f"otp:{TENANT_ID}:{OTP_PHONE}"


def _otp_tokens(**overrides: Any) -> list[dict[str, Any]]:
    common = {"tenant_id": TENANT_ID, "used": False, "verify_attempts": 0, "ttl": 600, **overrides}
    return [
        {"id": EMAIL_OTP_ID, "token_type": _OTP_TOKEN_TYPE, "email": OTP_EMAIL, "otp_code": OTP_CODE, **common},
        {
            "id": SMS_OTP_ID,
            "token_type": _SMS_OTP_TOKEN_TYPE,
            "email": OTP_PHONE,
            "otp_code_hash": hash_code(OTP_CODE),
            **common,
        },
    ]


@pytest.fixture
def otps():
    container = EtagContainer(partition_field="token_type")
    for doc in _otp_tokens():
        container.put(doc)
    manager = _manager_for(container)
    with (
        patch("src.multi_tenant.cosmos_client.get_cosmos_manager", return_value=manager),
        patch("src.multi_tenant.repositories.verification.get_cosmos_manager", return_value=manager),
    ):
        yield container


def _widget_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.tenant_id = TENANT_ID
    return ctx


# Each verifier submits the correct code and returns whether it was accepted.
# The widget endpoints also check that a credential (customer_token, phone)
# comes back exactly when the code is accepted.


async def _widget_email() -> bool:
    resp = await widget_verify_otp(OtpVerifyRequest(email=OTP_EMAIL, code=OTP_CODE), _widget_ctx())
    assert (resp.customer_token is not None) is resp.verified
    return resp.verified


async def _widget_sms() -> bool:
    resp = await widget_verify_sms_otp(SmsVerifyRequest(phone=OTP_PHONE, code=OTP_CODE), _widget_ctx())
    assert (resp.phone is not None) is resp.verified
    return resp.verified


async def _chat_email() -> bool:
    return await _verify_otp_for_conversation(OTP_EMAIL, OTP_CODE, TENANT_ID)


async def _chat_sms() -> bool:
    return await _verify_sms_otp_for_conversation(OTP_PHONE, OTP_CODE, TENANT_ID)


OTP_VERIFIERS = [
    pytest.param(_widget_email, EMAIL_OTP_ID, _OTP_TOKEN_TYPE, "Widget OTP verified", id="widget-email"),
    pytest.param(_widget_sms, SMS_OTP_ID, _SMS_OTP_TOKEN_TYPE, "SMS OTP verified", id="widget-sms"),
    pytest.param(_chat_email, EMAIL_OTP_ID, _OTP_TOKEN_TYPE, "In-conversation OTP verified", id="chat-email"),
    pytest.param(_chat_sms, SMS_OTP_ID, _SMS_OTP_TOKEN_TYPE, "In-conversation SMS OTP verified", id="chat-sms"),
]


@pytest.mark.parametrize(("verify", "token_id", "token_type", "verified_log"), OTP_VERIFIERS)
class TestOtpVerificationNeedsTheConsumption:
    async def test_correct_code_is_refused_when_consumption_loses(
        self, otps, verify, token_id, token_type, verified_log, caplog
    ):
        consume = AsyncMock(return_value=None)

        with (
            patch.object(VerificationTokenRepository, "consume_token", consume),
            caplog.at_level(logging.DEBUG),
        ):
            assert await verify() is False

        consume.assert_awaited_once_with(token_id, token_type)
        assert verified_log not in caplog.text

    async def test_two_concurrent_correct_codes_yield_exactly_one_success(
        self, otps, verify, token_id, token_type, verified_log
    ):
        results = await _concurrently(otps, verify(), verify())

        assert sorted(results) == [False, True]
        assert otps.stored(token_id)["used"] is True

    async def test_token_changed_during_consumption_is_refused_and_stays_redeemable(
        self, otps, verify, token_id, token_type, verified_log
    ):
        """For example a concurrent wrong guess incrementing verify_attempts."""
        otps.after_read = otps.touch

        assert await verify() is False
        assert otps.stored(token_id)["used"] is False

        otps.after_read = None
        assert await verify() is True
        assert otps.stored(token_id)["used"] is True

    async def test_single_correct_code_is_accepted_and_consumes_the_token(
        self, otps, verify, token_id, token_type, verified_log
    ):
        etag = otps.stored(token_id)["_etag"]

        assert await verify() is True

        assert otps.stored(token_id)["used"] is True
        assert otps.patch_calls == [
            {"item": token_id, "etag": etag, "match_condition": MatchConditions.IfNotModified},
        ]

    async def test_used_token_is_refused(self, otps, verify, token_id, token_type, verified_log):
        for doc in _otp_tokens(used=True):
            otps.put(doc)

        assert await verify() is False
        assert otps.patch_calls == []


async def test_email_otp_race_between_widget_and_chat_yields_one_success(otps):
    results = await _concurrently(otps, _widget_email(), _chat_email())

    assert sorted(results) == [False, True]
    assert otps.stored(EMAIL_OTP_ID)["used"] is True
