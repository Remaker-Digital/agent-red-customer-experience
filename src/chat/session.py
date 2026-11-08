# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""
Conversation lifecycle management for the Chat API.

Manages the full lifecycle of a customer conversation: creation, message
appending, idle timeout enforcement, turn counting, end conditions, and
metering. This is the bridge between the Chat API endpoints and the
underlying Cosmos DB ConversationDocument + ConversationMeter.

Lifecycle states:
    ACTIVE      → accepting messages, pipeline processes each turn
    COMPLETED   → customer ended, max turns reached, or idle timeout
    ESCALATED   → human agent takeover
    TIMED_OUT   → idle timeout (30 min inactivity)
    ERROR       → pipeline failure

Architecture references:
    - UI-UX-ARCHITECTURE-DECISIONS.md §3: Chat API Specification
    - Decision #24: Billable conversation definition
    - Decision #71-72: ConversationMeter
    - ConversationRepository: append_message, end_conversation, find_active

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, NamedTuple

from src.chat.models import (
    ChatMessage,
    ConversationStartRequest,
    ConversationStartResponse,
    ConversationStateResponse,
    EndConversationRequest,
    EndConversationResponse,
    MessageRole,
    SendMessageRequest,
    SendMessageResponse,
    VisitorIdentity,
)
from src.multi_tenant.conversation_meter import (
    MAX_TURNS,
    NON_BILLABLE_PREFIXES,
    ConversationEndReason,
    ConversationMeter,
)
from src.multi_tenant.cosmos_schema import (
    ContactAttribute,
    ContactAttributeType,
    ConversationDocument,
    ConversationStatus,
    TenantTier,
)
from src.multi_tenant.gdpr_services import PiiScrubber
from src.multi_tenant.repository import (
    ConversationRepository,
    DocumentNotFoundError,
    PreferencesRepository,
    TeamMemberRepository,
)
from src.multi_tenant.repositories.conversation import RMW_MAX_ATTEMPTS

logger = logging.getLogger(__name__)

# How long a tenant's pii_scrubbing value read from its preferences is reused
# before a write reads it again. A turn with the same or a newer configuration
# also refreshes it (set_pii_scrubber).
_PII_PREFERENCE_TTL_SECONDS = 60.0


def _merge_message_metadata(
    doc: dict[str, Any], message_id: str, metadata_patch: dict[str, Any],
) -> bool:
    """Merge ``metadata_patch`` into the metadata of one message of ``doc``.

    Returns False, leaving ``doc`` unchanged, when no message has ``message_id``.
    """
    messages = doc.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if msg.get("message_id") == message_id:
            msg["metadata"] = {**(msg.get("metadata") or {}), **metadata_patch}
            return True
    return False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConversationNotFoundError(Exception):
    """Raised when a conversation does not exist or belongs to another tenant."""

    def __init__(self, conversation_id: str, tenant_id: str) -> None:
        self.conversation_id = conversation_id
        self.tenant_id = tenant_id
        super().__init__(
            f"Conversation not found: {conversation_id} (tenant={tenant_id})"
        )


class ConversationNotActiveError(Exception):
    """Raised when an operation requires an active conversation."""

    def __init__(self, conversation_id: str, status: str) -> None:
        self.conversation_id = conversation_id
        self.status = status
        super().__init__(
            f"Conversation {conversation_id} is not active (status={status})"
        )


class TurnLimitReachedError(Exception):
    """Raised when a conversation has reached the maximum turn count."""

    def __init__(self, conversation_id: str, turn_count: int) -> None:
        self.conversation_id = conversation_id
        self.turn_count = turn_count
        super().__init__(
            f"Conversation {conversation_id} reached max turns ({turn_count})"
        )


class ConcurrencyExhaustedError(Exception):
    """Raised when ETag retries are exhausted on concurrent writes."""

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(
            f"Conversation {conversation_id}: max concurrent-write retries exhausted"
        )


class TrialLimitReachedError(Exception):
    """Raised when a trial tenant has exhausted their conversation allowance."""

    def __init__(self, tenant_id: str, limit: int) -> None:
        self.tenant_id = tenant_id
        self.limit = limit
        super().__init__(
            f"Trial tenant {tenant_id} has used all {limit} conversations"
        )


class InFlightResponseError(Exception):
    """Raised when a customer sends a new message while the AI is still responding.

    P1-2: Single in-flight response enforcement. The widget should retry
    with the same idempotency_key after retry_after_ms.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        super().__init__(
            f"Conversation {conversation_id} has a pending customer message"
        )


# ---------------------------------------------------------------------------
# Per-tenant PII scrubbing preference (architecture review S19)
# ---------------------------------------------------------------------------

class PiiScrubbingPreference(NamedTuple):
    """A tenant's ``pii_scrubbing`` setting and its configuration's ``activated_at``."""

    enabled: bool
    activated_at: str | None = None


#: Reads a tenant's ``pii_scrubbing`` preference: ``await resolver(tenant_id)``.
#: A bare bool is accepted too, as a setting with no activation time.
PiiScrubbingResolver = Callable[[str], Awaitable[PiiScrubbingPreference | bool]]


class _PiiScrubbingSetting(NamedTuple):
    enabled: bool
    stored_at: float  # time.monotonic()
    # When the configuration it came from was activated; orders settings (_kept_setting).
    activated_at: datetime | None = None


def _activation_time(value: Any) -> datetime | None:
    """A configuration's ``activated_at`` as an aware datetime, or None if absent or unreadable.

    Parsed rather than compared as text, so a different offset, suffix or
    precision cannot misorder two values. Without an offset it is UTC, as the
    services write it.
    """
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _kept_setting(current: _PiiScrubbingSetting | None, incoming: _PiiScrubbingSetting) -> _PiiScrubbingSetting:
    """The setting a tenant keeps when ``incoming`` arrives (a turn's record or a read).

    Ordered by when each setting's configuration was activated, so a turn that
    loaded its preferences, or a read that fetched them, before a newer
    configuration was activated never replaces that configuration's setting.
    activated_at is stamped by every activation, including restore_previous,
    which makes a lower version active again; version does not order that.

    - Both times known: the newer configuration wins; for the same one, the latest record.
    - Neither known: nothing to order by, so the latest record, as before ordering.
    - One known: they cannot be ordered. Scrubbing stays on if either has it
      (the privacy-safe choice), kept under the known time, so the next record
      or read of that configuration, or of a newer one, replaces it.
    """
    if current is None:
        return incoming
    if current.activated_at is not None and incoming.activated_at is not None:
        return current if incoming.activated_at < current.activated_at else incoming
    if current.activated_at is None and incoming.activated_at is None:
        return incoming
    return _PiiScrubbingSetting(
        current.enabled or incoming.enabled,
        incoming.stored_at,
        current.activated_at or incoming.activated_at,
    )


def _settled_read(
    before: _PiiScrubbingSetting | None, current: _PiiScrubbingSetting | None, read: _PiiScrubbingSetting
) -> _PiiScrubbingSetting:
    """The setting a tenant keeps when a read returns.

    ``before`` was recorded when the read started, ``current`` is recorded
    now. If the record did not change during the read (an older turn's
    record changes nothing) and the read fetched an older configuration than
    the one recorded, that one is no longer what get_current returns
    (restore_previous demoted it, leaving an earlier activation current).
    The read takes over under its own time, scrubbing if either says so, so
    the next record or read of it applies. Otherwise the read is kept like a
    turn's record (_kept_setting).
    """
    demoted = (
        current is before
        and current is not None
        and current.activated_at is not None
        and read.activated_at is not None
        and read.activated_at < current.activated_at
    )
    if demoted:
        return _PiiScrubbingSetting(current.enabled or read.enabled, read.stored_at, read.activated_at)
    return _kept_setting(current, read)


def _read_setting(preference: PiiScrubbingPreference | bool) -> _PiiScrubbingSetting:
    """The setting a resolver read; a bare bool has no activation time."""
    if isinstance(preference, bool):
        return _PiiScrubbingSetting(preference, time.monotonic())
    return _PiiScrubbingSetting(preference.enabled is True, time.monotonic(), _activation_time(preference.activated_at))


def pii_scrubbing_enabled(preferences: Any) -> bool:
    """Whether a tenant's preferences turn on scrubbing of its stored transcript.

    Only a stored ``pii_scrubbing`` of exactly True turns it on; a missing
    field is off. ``preferences`` is a PreferencesDocument or the raw
    preferences dict.
    """
    if isinstance(preferences, Mapping):
        return preferences.get("pii_scrubbing") is True
    return getattr(preferences, "pii_scrubbing", False) is True


def pii_scrubbing_setting(preferences: Any) -> bool | None:
    """The ``pii_scrubbing`` setting the preferences carry, or None without the field.

    None lets a caller tell preferences that do not say (for example the
    default document the chat routes use when loading fails) from a stored
    setting; any stored value other than True is off.
    """
    if isinstance(preferences, Mapping):
        if "pii_scrubbing" not in preferences:
            return None
        return preferences["pii_scrubbing"] is True
    value = getattr(preferences, "pii_scrubbing", None)
    return None if value is None else value is True


def configuration_activated_at(preferences: Any) -> Any:
    """When the configuration the preferences come from was activated (``activated_at``), or None."""
    if isinstance(preferences, Mapping):
        return preferences.get("activated_at")
    return getattr(preferences, "activated_at", None)


async def resolve_pii_scrubbing_preference(tenant_id: str) -> PiiScrubbingPreference:
    """Read a tenant's ``pii_scrubbing`` preference from its active configuration.

    This is the document the chat routes load for ChatPipeline.execute()
    (PreferencesRepository.get_current). A tenant without one has scrubbing
    off, as execute() has with default preferences, and no activation time.
    """
    raw = await PreferencesRepository().get_current(tenant_id) or {}
    return PiiScrubbingPreference(pii_scrubbing_enabled(raw), configuration_activated_at(raw))


# ---------------------------------------------------------------------------
# ConversationSession — lifecycle manager
# ---------------------------------------------------------------------------


class ConversationSession:
    """Manages the lifecycle of customer conversations.

    This service is the single entry point for conversation state changes.
    It coordinates between:
        - ConversationRepository (Cosmos DB persistence)
        - ConversationMeter (billing / metering)
        - Chat API endpoints (HTTP request/response translation)

    Thread-safety: conversation state lives in Cosmos DB. Multiple
    concurrent requests for the same conversation are safe because
    ConversationRepository uses atomic Cosmos DB patch operations. One
    instance serves every tenant, so the only in-memory state, each tenant's
    ``pii_scrubbing`` preference, is kept per tenant (architecture review S19).
    """

    def __init__(
        self,
        conversation_repo: ConversationRepository,
        conversation_meter: ConversationMeter | None = None,
        team_repo: TeamMemberRepository | None = None,
        customer_profile_repo: Any | None = None,
        pii_scrubbing_resolver: PiiScrubbingResolver | None = None,
    ) -> None:
        self._repo = conversation_repo
        self._meter = conversation_meter
        self._team_repo = team_repo
        self._customer_repo = customer_profile_repo
        self._pii_scrubber = PiiScrubber()
        self._pii_scrubbing_resolver = pii_scrubbing_resolver
        self._pii_scrubbing: dict[str, _PiiScrubbingSetting] = {}

    @property
    def conversation_repo(self) -> ConversationRepository:
        """The conversation repository, for routes that write a document field directly."""
        return self._repo

    def set_pii_scrubber(self, enabled: bool, *, tenant_id: str, activated_at: str | datetime | None = None) -> None:
        """Record whether one tenant's stored messages are scrubbed of PII.

        When enabled, that tenant's customer and AI message content is
        scrubbed for email addresses and phone numbers before persisting
        to Cosmos DB. The live response delivered to the customer is NOT
        affected, only the stored transcript. Other tenants' writes are
        never affected.

        Called by the pipeline at the start of each conversation turn,
        based on the tenant's ``pii_scrubbing`` config value, with
        ``activated_at`` from the same preferences. A setting from an older
        configuration than the one recorded never replaces it (_kept_setting).
        Writes for a tenant with no recorded value read it with the resolver.
        """
        incoming = _PiiScrubbingSetting(enabled, time.monotonic(), _activation_time(activated_at))
        self._pii_scrubbing[tenant_id] = _kept_setting(self._pii_scrubbing.get(tenant_id), incoming)

    async def _pii_scrubbing_enabled(self, tenant_id: str) -> bool:
        """The tenant's ``pii_scrubbing`` preference for a write.

        Uses the value recorded for the tenant while it is younger than
        _PII_PREFERENCE_TTL_SECONDS, otherwise reads it with the resolver.
        If that read fails, the value recorded when it fails is used,
        including one a turn recorded during the read; with none, the write
        is scrubbed (the privacy-safe choice) and nothing is recorded.
        A read is kept against whatever is recorded when it returns
        (_settled_read), so a turn recorded during the read wins only with a
        newer configuration.
        Without a resolver, a tenant with no recorded value is not scrubbed.
        """
        setting = self._pii_scrubbing.get(tenant_id)
        resolver = self._pii_scrubbing_resolver
        if resolver is None:
            return setting is not None and setting.enabled
        if setting is not None and time.monotonic() - setting.stored_at < _PII_PREFERENCE_TTL_SECONDS:
            return setting.enabled
        try:
            preference = await resolver(tenant_id)
        except Exception:
            return self._scrubbing_after_failed_read(tenant_id)
        kept = _settled_read(setting, self._pii_scrubbing.get(tenant_id), _read_setting(preference))
        self._pii_scrubbing[tenant_id] = kept
        return kept.enabled

    def _scrubbing_after_failed_read(self, tenant_id: str) -> bool:
        """The last known value for a write whose preference read failed; with none, scrub."""
        known = self._pii_scrubbing.get(tenant_id)
        if known is None:
            logger.warning(
                "pii_scrubbing preference read failed for tenant %s and none is known; scrubbing this write",
                tenant_id,
                exc_info=True,
            )
            return True
        logger.warning(
            "pii_scrubbing preference read failed for tenant %s; using the last known value",
            tenant_id,
            exc_info=True,
        )
        return known.enabled

    async def _stored_content(self, tenant_id: str, content: str) -> str:
        """Message content as stored for the tenant: scrubbed if its preference says so."""
        if await self._pii_scrubbing_enabled(tenant_id):
            return self._pii_scrubber.scrub_text(content)
        return content

    # -------------------------------------------------------------------
    # Create conversation
    # -------------------------------------------------------------------

    async def start_conversation(
        self,
        tenant_id: str,
        request: ConversationStartRequest,
        *,
        tier: TenantTier | None = None,
    ) -> ConversationStartResponse:
        """Start a new conversation.

        Creates a ConversationDocument in Cosmos DB with ACTIVE status.
        If the request includes an initial_message, it is appended as
        the first customer message.

        Args:
            tenant_id: Authenticated tenant identifier.
            request: Conversation start parameters.
            tier: Tenant tier (for billing context).

        Returns:
            ConversationStartResponse with assigned IDs and stream URLs.

        Raises:
            TrialLimitReachedError: If the tenant is on the trial tier
                and has used all included conversations.
        """
        # Trial cap enforcement — block new conversations when trial
        # allowance is exhausted (WI #119). Trial tenants have no packs
        # and no overage billing, so this is a hard stop.
        if tier == TenantTier.TRIAL and self._meter:
            try:
                from src.multi_tenant.entitlement_service import get_entitlement_service
                defaults = await get_entitlement_service().get_tier_config(TenantTier.TRIAL.value)
                trial_limit = defaults.get("included_conversations", 50)
                dashboard = await self._meter.get_usage_dashboard(
                    tenant_id, tier,
                )
                if dashboard.total_conversations >= trial_limit:
                    raise TrialLimitReachedError(tenant_id, trial_limit)
            except TrialLimitReachedError:
                raise
            except Exception:
                # Non-fatal — don't block conversations on meter errors
                logger.debug("Trial cap check failed for %s, allowing", tenant_id)

        conversation_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()

        # ADR-004: Resolve canonical customer identity (find-or-create).
        # Falls back to legacy resolver when no customer_profile_repo is configured.
        canonical_customer_id: str | None = None
        if self._customer_repo:
            canonical_customer_id, customer_id = await _resolve_canonical_customer_id(
                request.visitor, tenant_id, self._customer_repo,
            )
        else:
            customer_id = _resolve_customer_id(request.visitor)

        # AUTH-5: Extract verification flag from metadata (set by endpoint)
        customer_verified = bool(
            request.metadata and request.metadata.get("customer_verified")
        )

        # SPEC-1606: conversations start non-billable; promoted on first AI response
        is_billable = False

        # Build initial messages list
        messages: list[dict[str, Any]] = []
        message_count = 0
        turn_count = 0

        if request.initial_message:
            msg_id = str(uuid.uuid4())
            messages.append({
                "role": MessageRole.CUSTOMER.value,
                # Stored per the tenant's pii_scrubbing preference, like every customer message (S19).
                "content": await self._stored_content(tenant_id, request.initial_message),
                "timestamp": now,
                "message_id": msg_id,
            })
            message_count = 1

        # SPEC-1862: Set actor/channel metadata for team-member conversations
        target_agent_id = getattr(request, "target_agent_id", None) or ""
        actor_type = "team_member" if target_agent_id else "customer"
        channel_origin = "admin_console" if target_agent_id else "widget"

        doc = ConversationDocument(
            id=conversation_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            status=ConversationStatus.ACTIVE,
            customer_id=customer_id,
            canonical_customer_id=canonical_customer_id,
            customer_verified=customer_verified,
            is_billable=is_billable,
            message_count=message_count,
            turn_count=turn_count,
            agents_invoked=[],
            messages=messages,
            started_at=now,
            last_activity_at=now,
            actor_type=actor_type,
            channel_origin=channel_origin,
            target_agent_id=target_agent_id,
        )

        await self._repo.create(tenant_id, doc)

        logger.info(
            "Conversation started: %s (tenant=%s, customer=%s)",
            conversation_id,
            tenant_id,
            customer_id or "anonymous",
        )

        return ConversationStartResponse(
            conversation_id=conversation_id,
            stream_url=f"/api/chat/stream/{conversation_id}",
            ws_url=f"/ws/chat/{conversation_id}",
            created_at=now,
        )

    # -------------------------------------------------------------------
    # Append customer message
    # -------------------------------------------------------------------

    async def add_customer_message(
        self,
        tenant_id: str,
        request: SendMessageRequest,
    ) -> SendMessageResponse:
        """Append a customer message to an active conversation.

        Validates that the conversation is active and under the turn limit,
        then atomically appends the message via Cosmos DB patch.

        Args:
            tenant_id: Authenticated tenant identifier.
            request: Message content and conversation_id.

        Returns:
            SendMessageResponse with the assigned message_id and turn count.

        Raises:
            ConversationNotFoundError: Conversation does not exist for this tenant.
            ConversationNotActiveError: Conversation is not in ACTIVE status.
            TurnLimitReachedError: Conversation has reached MAX_TURNS.
            ConcurrencyExhaustedError: Every ETag-conditional write attempt
                lost to a concurrent writer.
        """
        # WI-3030 v2.1: Use _get_writable_conversation so customers can
        # continue chatting after async email-bridge escalation.
        doc = await self._get_writable_conversation(tenant_id, request.conversation_id)

        turn_count = doc.get("turn_count", 0)
        if turn_count >= MAX_TURNS:
            raise TurnLimitReachedError(request.conversation_id, turn_count)

        now = datetime.now(UTC).isoformat()
        message_id = str(uuid.uuid4())

        # PII scrubbing: redact emails/phones from stored transcript
        stored_content = await self._stored_content(tenant_id, request.content)

        message_dict: dict[str, Any] = {
            "role": MessageRole.CUSTOMER.value,
            "content": stored_content,
            "timestamp": now,
            "message_id": message_id,
        }
        if request.metadata:
            message_dict["metadata"] = request.metadata

        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        try:
            await self._repo.append_message(
                tenant_id=tenant_id,
                conversation_id=request.conversation_id,
                message=message_dict,
            )
        except CosmosAccessConditionFailedError as exc:
            # The ETag retries in append_message were all lost to other writers.
            raise ConcurrencyExhaustedError(request.conversation_id) from exc

        # message_count was atomically incremented by append_message;
        # turn_count increments when the AI responds (in add_ai_message).
        new_message_count = doc.get("message_count", 0) + 1

        logger.debug(
            "Customer message appended: conv=%s msg=%s (#%d)",
            request.conversation_id,
            message_id,
            new_message_count,
        )

        return SendMessageResponse(
            message_id=message_id,
            conversation_id=request.conversation_id,
            turn_count=turn_count,
            accepted=True,
        )

    async def add_customer_message_idempotent(
        self,
        tenant_id: str,
        request: SendMessageRequest,
        max_retries: int = 3,
    ) -> SendMessageResponse:
        """Append a customer message with Cosmos conditional-write idempotency.

        Uses ETag/If-Match optimistic concurrency (P1-2). On conflict the
        full read-check-append cycle re-runs against the fresh document so
        idempotency, in-flight, and turn-limit checks are always consistent.

        Falls back to :meth:`add_customer_message` when no idempotency_key
        is provided (backward compatible with old widget versions).
        """
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        if not request.idempotency_key:
            return await self.add_customer_message(tenant_id, request)

        for _attempt in range(max_retries):
            # WI-3030 v2.1: Use _get_writable_conversation so customers
            # can continue chatting after async email-bridge escalation.
            doc = await self._get_writable_conversation(
                tenant_id, request.conversation_id,
            )
            etag = doc.get("_etag", "")
            messages = doc.get("messages", [])

            # Idempotency check: key already appended by a prior write
            for msg in messages:
                meta = msg.get("metadata") or {}
                if meta.get("idempotency_key") == request.idempotency_key:
                    return SendMessageResponse(
                        message_id=msg["message_id"],
                        conversation_id=request.conversation_id,
                        turn_count=doc.get("turn_count", 0),
                        accepted=True,
                    )

            # In-flight check: last message is customer with no AI reply
            if messages and messages[-1].get("role") == MessageRole.CUSTOMER.value:
                raise InFlightResponseError(request.conversation_id)

            # Turn limit check
            turn_count = doc.get("turn_count", 0)
            if turn_count >= MAX_TURNS:
                raise TurnLimitReachedError(request.conversation_id, turn_count)

            # Build message
            now = datetime.now(UTC).isoformat()
            message_id = str(uuid.uuid4())
            stored_content = await self._stored_content(tenant_id, request.content)

            message_dict: dict[str, Any] = {
                "role": MessageRole.CUSTOMER.value,
                "content": stored_content,
                "timestamp": now,
                "message_id": message_id,
                "metadata": {
                    **(request.metadata or {}),
                    "idempotency_key": request.idempotency_key,
                },
            }

            messages.append(message_dict)
            doc["messages"] = messages
            doc["message_count"] = len(messages)
            doc["last_activity_at"] = now

            try:
                await self._repo.replace_with_etag(
                    tenant_id, request.conversation_id, doc, etag,
                )
                logger.debug(
                    "Idempotent message appended: conv=%s msg=%s key=%s",
                    request.conversation_id, message_id,
                    request.idempotency_key[:8],
                )
                return SendMessageResponse(
                    message_id=message_id,
                    conversation_id=request.conversation_id,
                    turn_count=turn_count,
                    accepted=True,
                )
            except CosmosAccessConditionFailedError:
                logger.debug(
                    "ETag conflict on idempotent append (attempt %d): conv=%s",
                    _attempt + 1, request.conversation_id,
                )
                continue

        raise ConcurrencyExhaustedError(request.conversation_id)

    # -------------------------------------------------------------------
    # Append AI response
    # -------------------------------------------------------------------

    async def add_ai_message(
        self,
        tenant_id: str,
        conversation_id: str,
        content: str,
        *,
        agents_invoked: list[str] | None = None,
        model_used: str | None = None,
        critic_passed: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append an AI-generated response to the conversation.

        Called by the pipeline orchestrator after the full response is
        generated and (optionally) validated by the Critic. Appends the
        message, increments turn_count, and updates pipeline trace fields
        in one ETag-conditional write, so a message or feedback another
        writer stores meanwhile is kept.

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Target conversation.
            content: AI response text.
            agents_invoked: Pipeline agents that participated.
            model_used: Primary model used for generation.
            critic_passed: Whether the Critic approved the response.
            metadata: Optional metadata (latency, tokens, etc.).

        Returns:
            The assigned message_id.

        Raises:
            ConcurrencyExhaustedError: Every ETag-conditional write attempt
                lost to a concurrent writer; the reply is not stored, and
                this is logged as an error.
        """
        now = datetime.now(UTC).isoformat()
        message_id = str(uuid.uuid4())

        # PII scrubbing: redact emails/phones from stored AI response
        stored_content = await self._stored_content(tenant_id, content)

        message_dict: dict[str, Any] = {
            "role": MessageRole.AI.value,
            "content": stored_content,
            "timestamp": now,
            "message_id": message_id,
        }
        if metadata:
            message_dict["metadata"] = metadata

        metadata_updates: dict[str, Any] = {}
        if agents_invoked is not None:
            metadata_updates["agents_invoked"] = agents_invoked
        if model_used is not None:
            metadata_updates["model_used"] = model_used
        if critic_passed is not None:
            metadata_updates["critic_passed"] = critic_passed
        # SPEC-1606: promote to billable on first AI response (eligible IDs only)
        if not conversation_id.startswith(NON_BILLABLE_PREFIXES):
            metadata_updates["is_billable"] = True

        # SPEC-1843: read-modify-write, since Cosmos patch cannot append to
        # the encrypted messages field. ETag-conditional with a bounded retry
        # (review F33); turn_count is counted from the document as written.
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        try:
            await self._repo.append_message_with_metadata(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                message=message_dict,
                metadata_updates=metadata_updates,
                count_turn=True,
            )
        except CosmosAccessConditionFailedError as exc:
            logger.error(
                "AI reply %s not stored on conversation %s (tenant=%s): "
                "every ETag-conditional write lost to a concurrent writer",
                message_id,
                conversation_id,
                tenant_id,
            )
            raise ConcurrencyExhaustedError(conversation_id) from exc

        logger.debug(
            "AI message appended: conv=%s msg=%s critic=%s",
            conversation_id,
            message_id,
            critic_passed,
        )

        return message_id

    # -------------------------------------------------------------------
    # Identity preprocessor system message (P0-AUTH-FIX)
    # -------------------------------------------------------------------

    async def add_system_identity_message(
        self,
        tenant_id: str,
        conversation_id: str,
        content: str,
    ) -> str:
        """Append a system-generated identity message (OTP flow).

        These messages are mechanical responses from the identity
        preprocessor (email confirmation, OTP validation results, etc.)
        and are stored as AI messages so the widget displays them in
        the chat bubble. They do NOT increment turn_count since no
        AI pipeline was invoked.

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Target conversation.
            content: System message text.

        Returns:
            The assigned message_id.
        """
        now = datetime.now(UTC).isoformat()
        message_id = str(uuid.uuid4())

        message_dict: dict[str, Any] = {
            "role": MessageRole.AI.value,
            "content": content,
            "timestamp": now,
            "message_id": message_id,
            "metadata": {"source": "identity_preprocessor"},
        }

        # SPEC-1843: Use append_message (read-modify-write) to preserve encryption.
        await self._repo.append_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message=message_dict,
        )

        logger.debug(
            "Identity system message appended: conv=%s msg=%s",
            conversation_id,
            message_id,
        )

        return message_id

    # -------------------------------------------------------------------
    # Patch message metadata (P3-1: quality score persistence)
    # -------------------------------------------------------------------

    async def patch_message_metadata(
        self,
        tenant_id: str,
        conversation_id: str,
        message_id: str,
        metadata_patch: dict[str, Any],
    ) -> None:
        """Patch metadata on an existing message within a conversation.

        Uses ETag-safe read-modify-write to merge new metadata fields
        into the message's existing metadata dict: the document is read
        (decrypted) with ``read`` and replaced under If-Match with
        ``replace_with_etag``, so a message or feedback another writer
        stores meanwhile is kept. On a 412 the document is read again and
        the patch reapplied, up to ``RMW_MAX_ATTEMPTS`` attempts (the bound
        the repository uses for its own transcript writes).
        Non-fatal on conflict (caller should catch and log).

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Conversation containing the message.
            message_id: The specific message to patch.
            metadata_patch: Dict of fields to merge into message metadata.

        Raises:
            CosmosAccessConditionFailedError: Every attempt lost to a
                concurrent writer; the stored document is unchanged.
            DocumentNotFoundError: The conversation does not exist.
        """
        from azure.cosmos.exceptions import CosmosAccessConditionFailedError

        for attempt in range(1, RMW_MAX_ATTEMPTS + 1):
            doc = await self._repo.read(tenant_id, conversation_id)
            etag = doc.get("_etag", "")
            if not _merge_message_metadata(doc, message_id, metadata_patch):
                logger.warning(
                    "patch_message_metadata: message %s not found in conv %s",
                    message_id, conversation_id,
                )
                return
            try:
                await self._repo.replace_with_etag(
                    tenant_id, conversation_id, doc, etag,
                )
                return
            except CosmosAccessConditionFailedError:
                if attempt == RMW_MAX_ATTEMPTS:
                    raise
                logger.debug(
                    "ETag conflict on message metadata patch (attempt %d): conv=%s",
                    attempt, conversation_id,
                )

    # -------------------------------------------------------------------
    # End conversation
    # -------------------------------------------------------------------

    async def end_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        request: EndConversationRequest | None = None,
        *,
        reason: ConversationEndReason = ConversationEndReason.CUSTOMER_ENDED,
    ) -> EndConversationResponse:
        """End an active conversation.

        Sets the conversation status based on the end reason, then
        meters the conversation for billing (if billable).

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Conversation to end.
            request: Optional end request with feedback.
            reason: Why the conversation is ending.

        Returns:
            EndConversationResponse with final state.

        Raises:
            ConversationNotFoundError: Conversation not found.
            ConversationNotActiveError: Already ended.
        """
        doc = await self._get_active_conversation(tenant_id, conversation_id)

        # Map end reason to conversation status
        status = _end_reason_to_status(reason)
        await self._repo.end_conversation(tenant_id, conversation_id, status)

        # Store feedback if provided
        if request and (request.feedback_rating or request.feedback_text):
            feedback_ops: list[dict[str, Any]] = []
            if request.feedback_rating is not None:
                feedback_ops.append(
                    {"op": "set", "path": "/feedback_rating", "value": request.feedback_rating}
                )
            if request.feedback_text is not None:
                feedback_ops.append(
                    {"op": "set", "path": "/feedback_text", "value": request.feedback_text}
                )
            if request.reason is not None:
                feedback_ops.append(
                    {"op": "set", "path": "/end_reason", "value": request.reason}
                )
            if feedback_ops:
                await self._repo.patch(tenant_id, conversation_id, feedback_ops)

        # Meter the conversation for billing
        is_billable = doc.get("is_billable", True)
        if is_billable and self._meter:
            try:
                await self._meter.meter_conversation(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                )
            except Exception:
                logger.exception(
                    "Failed to meter conversation %s (tenant=%s) — "
                    "billing reconciliation will catch this",
                    conversation_id,
                    tenant_id,
                )

        # P3-1: Quality aggregate + regression alert (shared closeout helper)
        try:
            from src.chat.quality_closeout import evaluate_quality_and_alert
            await evaluate_quality_and_alert(tenant_id, conversation_id, self._repo)
        except Exception:
            logger.warning(
                "Quality closeout failed (non-fatal): conv=%s", conversation_id,
                exc_info=True,
            )

        # Calculate duration
        duration_seconds = _calculate_duration(
            doc.get("started_at", ""),
            datetime.now(UTC).isoformat(),
        )

        logger.info(
            "Conversation ended: %s reason=%s status=%s billable=%s",
            conversation_id,
            reason.value,
            status.value,
            is_billable,
        )

        return EndConversationResponse(
            conversation_id=conversation_id,
            status=status.value,
            turn_count=doc.get("turn_count", 0),
            duration_seconds=duration_seconds,
            is_billable=is_billable,
        )

    # -------------------------------------------------------------------
    # Get conversation state
    # -------------------------------------------------------------------

    async def get_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> ConversationStateResponse:
        """Get the current state of a conversation.

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Conversation to retrieve.

        Returns:
            ConversationStateResponse with full message history.

        Raises:
            ConversationNotFoundError: Conversation not found.
        """
        doc = await self._get_conversation(tenant_id, conversation_id)

        messages = [
            ChatMessage(
                role=MessageRole(m["role"]),
                content=m["content"],
                timestamp=m.get("timestamp", ""),
                message_id=m.get("message_id"),
                metadata=m.get("metadata"),
            )
            for m in doc.get("messages", [])
        ]

        return ConversationStateResponse(
            conversation_id=conversation_id,
            status=doc.get("status", ConversationStatus.ACTIVE.value),
            turn_count=doc.get("turn_count", 0),
            message_count=doc.get("message_count", 0),
            messages=messages,
            is_escalated=doc.get("status") == ConversationStatus.ESCALATED.value,
            customer_verified=bool(doc.get("customer_verified", False)),
            target_agent_id=doc.get("target_agent_id", ""),
            created_at=doc.get("started_at", ""),
            last_activity_at=doc.get("last_activity_at", ""),
        )

    # -------------------------------------------------------------------
    # Idle timeout check
    # -------------------------------------------------------------------

    async def check_idle_timeout(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> ConversationEndReason | None:
        """Check if an active conversation should be ended due to inactivity.

        Delegates to ConversationMeter.should_end_conversation() for the
        idle timeout and max turns checks.

        Returns:
            The end reason if the conversation should end, or None if active.
        """
        doc = await self._get_conversation(tenant_id, conversation_id)

        if doc.get("status") != ConversationStatus.ACTIVE.value:
            return None

        return ConversationMeter.should_end_conversation(
            last_activity_at=doc.get("last_activity_at", ""),
            turn_count=doc.get("turn_count", 0),
        )

    # -------------------------------------------------------------------
    # Escalation — agent routing
    # -------------------------------------------------------------------

    async def find_best_agent_for_category(
        self,
        tenant_id: str,
        category: str,
    ) -> str | None:
        """Find an active escalation_agent who handles the given category.

        Returns the first matching agent's document ID, or falls back to
        agents handling ``general_inquiry``.  Returns ``None`` if no
        suitable agent is available.

        WI-3030 S259: Simplified for async email-bridge model — no
        concurrency caps or workload balancing needed since the agent
        responds via email, not live chat.
        """
        if not self._team_repo:
            logger.debug("No team_repo configured — cannot auto-assign")
            return None

        members = await self._team_repo.list_members(tenant_id)

        # Filter to active escalation agents who handle this category
        candidates = [
            m for m in members
            if m.get("role") == "escalation_agent"
            and m.get("is_active", True)
            and category in (m.get("escalation_categories") or [])
        ]

        # Fallback: try general_inquiry agents
        if not candidates and category != "general_inquiry":
            candidates = [
                m for m in members
                if m.get("role") == "escalation_agent"
                and m.get("is_active", True)
                and "general_inquiry" in (m.get("escalation_categories") or [])
            ]

        if not candidates:
            return None

        return candidates[0]["id"]

    async def find_superadmin_email(self, tenant_id: str) -> str | None:
        """Return the email address of the tenant's superadmin.

        Used as the fallback recipient for escalation notifications when
        no specific escalation agent is assigned.

        Returns the email, or None if no team_repo is configured or no
        superadmin exists.
        """
        if not self._team_repo:
            return None

        members = await self._team_repo.list_members(tenant_id)
        for m in members:
            if m.get("role") == "superadmin" and m.get("is_active", True):
                return m.get("email")
        return None

    # -------------------------------------------------------------------
    # Escalation — conversation state
    # -------------------------------------------------------------------

    async def escalate_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        escalation_reason: str | None = None,
        escalation_category: str | None = None,
        assigned_to: str | None = None,
    ) -> None:
        """Escalate a conversation to a human agent.

        Sets the conversation status to ESCALATED and appends a system
        message recording the escalation event.  Optionally sets the
        escalation category and assigns the conversation to a specific
        team member.

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Conversation to escalate.
            escalation_reason: Optional reason from the Escalation agent.
            escalation_category: Optional category (from ESCALATION_CATEGORIES).
            assigned_to: Optional human agent ID for auto-assignment.
        """
        await self._get_active_conversation(tenant_id, conversation_id)

        now = datetime.now(UTC).isoformat()
        system_msg: dict[str, Any] = {
            "role": MessageRole.SYSTEM.value,
            "content": "Conversation escalated to a human agent.",
            "timestamp": now,
            "message_id": str(uuid.uuid4()),
        }
        if escalation_reason:
            metadata: dict[str, Any] = {"escalation_reason": escalation_reason}
            if escalation_category:
                metadata["escalation_category"] = escalation_category
            if assigned_to:
                metadata["assigned_to"] = assigned_to
            system_msg["metadata"] = metadata

        # SPEC-1843: Append message via read-modify-write (preserves encryption),
        # then patch non-encrypted metadata fields separately.
        metadata_updates: dict[str, Any] = {}
        if escalation_category:
            metadata_updates["escalation_category"] = escalation_category
        if assigned_to:
            metadata_updates["assigned_to"] = assigned_to

        await self._repo.append_message_with_metadata(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message=system_msg,
            metadata_updates={
                "status": ConversationStatus.ESCALATED.value,
                **metadata_updates,
            },
        )

        logger.info(
            "Conversation escalated: %s (tenant=%s, reason=%s, category=%s, assigned=%s)",
            conversation_id,
            tenant_id,
            escalation_reason or "not specified",
            escalation_category or "none",
            assigned_to or "unassigned",
        )

        # P3-1: Quality aggregate + regression alert at escalation closeout
        try:
            from src.chat.quality_closeout import evaluate_quality_and_alert
            await evaluate_quality_and_alert(tenant_id, conversation_id, self._repo)
        except Exception:
            logger.warning(
                "Quality closeout failed (non-fatal): conv=%s", conversation_id,
                exc_info=True,
            )

    # -------------------------------------------------------------------
    # Resume from existing active conversation
    # -------------------------------------------------------------------

    async def find_active_conversation(
        self,
        tenant_id: str,
        customer_id: str,
    ) -> ConversationStateResponse | None:
        """Find an existing active conversation for a customer.

        Used when a returning customer opens the widget — if they have
        an active conversation, resume it instead of starting a new one.

        Returns:
            The active conversation state, or None if no active conversation.
        """
        doc = await self._repo.find_active(tenant_id, customer_id)
        if doc is None:
            return None

        return await self.get_conversation(tenant_id, doc["conversation_id"])

    # -------------------------------------------------------------------
    # Metadata update (SPEC-1531 — pipeline trace persistence)
    # -------------------------------------------------------------------

    async def update_conversation_metadata(
        self,
        tenant_id: str,
        conversation_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Patch arbitrary metadata fields on a conversation document.

        Used by the pipeline orchestrator to persist the pipeline_trace
        (SPEC-1531) after each AI response turn. Each key in ``metadata``
        becomes a Cosmos DB set operation on the document root.

        Args:
            tenant_id: Tenant identifier.
            conversation_id: Target conversation.
            metadata: Dict of field_name → value to set on the document.
        """
        if not metadata:
            return

        operations: list[dict[str, Any]] = [
            {"op": "set", "path": f"/{key}", "value": value}
            for key, value in metadata.items()
        ]

        await self._repo.patch(
            tenant_id=tenant_id,
            document_id=conversation_id,
            operations=operations,
        )

        logger.debug(
            "Conversation metadata updated: conv=%s keys=%s",
            conversation_id,
            list(metadata.keys()),
        )

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    async def _get_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Read a conversation document, raising domain exceptions."""
        try:
            return await self._repo.read(tenant_id, conversation_id)
        except DocumentNotFoundError:
            raise ConversationNotFoundError(conversation_id, tenant_id) from None

    async def _get_active_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Read a conversation and verify it is ACTIVE."""
        doc = await self._get_conversation(tenant_id, conversation_id)
        status = doc.get("status", "")
        if status != ConversationStatus.ACTIVE.value:
            raise ConversationNotActiveError(conversation_id, status)
        return doc

    async def _get_writable_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        """Read a conversation that accepts customer messages.

        ACTIVE and ESCALATED conversations are writable.  ESCALATED
        conversations continue the AI chat while the async email-bridge
        handles human follow-up separately (WI-3030 v2.1).
        """
        doc = await self._get_conversation(tenant_id, conversation_id)
        status = doc.get("status", "")
        writable_statuses = {
            ConversationStatus.ACTIVE.value,
            ConversationStatus.ESCALATED.value,
        }
        if status not in writable_statuses:
            raise ConversationNotActiveError(conversation_id, status)
        return doc


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_session: ConversationSession | None = None


def get_conversation_session() -> ConversationSession:
    """Get or create the module-level ConversationSession singleton.

    Returns a session with default-constructed dependencies. For testing,
    use ConversationSession() directly with injected mocks.
    """
    global _session
    if _session is None:
        _session = ConversationSession(
            conversation_repo=ConversationRepository(),
        )
    return _session


def configure_conversation_session(
    conversation_repo: ConversationRepository,
    conversation_meter: ConversationMeter | None = None,
    team_repo: TeamMemberRepository | None = None,
    customer_profile_repo: Any | None = None,
    pii_scrubbing_resolver: PiiScrubbingResolver | None = resolve_pii_scrubbing_preference,
) -> ConversationSession:
    """Configure the module-level singleton with explicit dependencies.

    Called during app startup (main.py) after Cosmos DB and metering
    services are initialized. A tenant's ``pii_scrubbing`` preference is
    read from its active configuration by default, so its first message in
    this process already follows it.
    """
    global _session
    _session = ConversationSession(
        conversation_repo=conversation_repo,
        conversation_meter=conversation_meter,
        team_repo=team_repo,
        customer_profile_repo=customer_profile_repo,
        pii_scrubbing_resolver=pii_scrubbing_resolver,
    )
    return _session


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_customer_id(visitor: VisitorIdentity | None) -> str | None:
    """Extract customer_id from visitor identity, if available.

    Legacy synchronous resolver — returns the raw visitor identifier
    (Shopify GID, email, or None).  Used when no CustomerProfileRepository
    is available (backward compat).
    """
    if visitor is None:
        return None
    return visitor.customer_id or visitor.email or None


async def _resolve_canonical_customer_id(
    visitor: VisitorIdentity | None,
    tenant_id: str,
    customer_repo: Any,
) -> tuple[str | None, str | None]:
    """Resolve visitor identity to a canonical customer ID (ADR-004).

    Implements find-or-create:
      1. If visitor has a cid_ prefixed customer_id, use it directly.
      2. If visitor has a Shopify customer GID, resolve via contact_attributes.
      3. If visitor has an email, resolve via contact_attributes.
      4. If no match found, create a new canonical profile.
      5. Anonymous visitors → (None, None).

    Returns:
        (canonical_customer_id, legacy_customer_id) tuple.
        legacy_customer_id preserves the original visitor identifier for
        backward compatibility on the ConversationDocument.
    """
    from src.multi_tenant.repositories.customer import generate_canonical_id

    if visitor is None:
        return None, None

    legacy_id = visitor.customer_id or visitor.email or None
    now = datetime.now(UTC).isoformat()

    # 1. Direct canonical_id (future: widget sends it)
    if visitor.customer_id and visitor.customer_id.startswith("cid_"):
        return visitor.customer_id, visitor.customer_id

    # 2. Shopify customer GID lookup
    if visitor.customer_id:
        canonical = await customer_repo.resolve_by_attribute(
            tenant_id, ContactAttributeType.SHOPIFY_CUSTOMER_GID, visitor.customer_id,
        )
        if canonical:
            return canonical, legacy_id

        # Auto-create profile for new Shopify customer.
        canonical = generate_canonical_id()
        attrs = [ContactAttribute(
            attribute_type=ContactAttributeType.SHOPIFY_CUSTOMER_GID,
            value=visitor.customer_id,
            verified=False,
            source="shopify_session",
            added_at=now,
        )]
        if visitor.email:
            attrs.append(ContactAttribute(
                attribute_type=ContactAttributeType.EMAIL,
                value=visitor.email,
                verified=False,
                source="shopify_session",
                added_at=now,
            ))
        await customer_repo.create_profile_with_canonical_id(
            tenant_id, canonical, contact_attributes=attrs,
        )
        # Post-create race guard: re-resolve to detect concurrent writes.
        # If another writer created a profile for the same attribute,
        # the query returns the earlier writer's canonical_id.  We keep
        # the first-writer-wins profile and orphan ours (it will have no
        # conversations linked and can be cleaned up asynchronously).
        winner = await customer_repo.resolve_by_attribute(
            tenant_id, ContactAttributeType.SHOPIFY_CUSTOMER_GID, visitor.customer_id,
        )
        return (winner or canonical), legacy_id

    # 3. Email lookup
    if visitor.email:
        canonical = await customer_repo.resolve_by_attribute(
            tenant_id, ContactAttributeType.EMAIL, visitor.email,
        )
        if canonical:
            return canonical, legacy_id

        # Auto-create profile for new email customer.
        canonical = generate_canonical_id()
        await customer_repo.create_profile_with_canonical_id(
            tenant_id, canonical, contact_attributes=[
                ContactAttribute(
                    attribute_type=ContactAttributeType.EMAIL,
                    value=visitor.email,
                    verified=False,
                    source="self_asserted",
                    added_at=now,
                ),
            ],
        )
        # Post-create race guard (same pattern as Shopify path)
        winner = await customer_repo.resolve_by_attribute(
            tenant_id, ContactAttributeType.EMAIL, visitor.email,
        )
        return (winner or canonical), legacy_id

    # 4. Anonymous — no canonical_id
    return None, None


def _end_reason_to_status(reason: ConversationEndReason) -> ConversationStatus:
    """Map a ConversationEndReason to the appropriate ConversationStatus."""
    mapping = {
        ConversationEndReason.CUSTOMER_ENDED: ConversationStatus.RESOLVED,
        ConversationEndReason.IDLE_TIMEOUT: ConversationStatus.TIMED_OUT,
        ConversationEndReason.ESCALATED: ConversationStatus.ESCALATED,
        ConversationEndReason.MAX_TURNS: ConversationStatus.RESOLVED,
        ConversationEndReason.ERROR: ConversationStatus.ERROR,
    }
    return mapping.get(reason, ConversationStatus.RESOLVED)


def _calculate_duration(started_at: str, ended_at: str) -> int | None:
    """Calculate conversation duration in seconds from ISO 8601 timestamps."""
    if not started_at or not ended_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(ended_at)
        return max(0, int((end - start).total_seconds()))
    except (ValueError, TypeError):
        return None
