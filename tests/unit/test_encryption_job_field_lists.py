# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""The encryption jobs list the same fields as the repositories encrypt.

The migration, DEK rotation and monitor jobs each keep their own
ENCRYPTED_COLLECTIONS. A field the repository encrypts but a job leaves out
is not migrated, is not monitored, and is not re-wrapped on DEK rotation, so
it becomes unreadable once the old DEK is retired. identity_email and
identity_phone (decision 92) were the first fields to drift.
"""

from __future__ import annotations

import importlib

import pytest

from src.multi_tenant import cosmos_schema as schema
from src.multi_tenant.repositories.conversation import ConversationRepository
from src.multi_tenant.repositories.customer import CustomerProfileRepository
from src.multi_tenant.repositories.knowledge import KnowledgeBaseRepository
from src.multi_tenant.repositories.memory import MemoryVectorRepository
from src.multi_tenant.repositories.preferences import PreferencesRepository
from src.multi_tenant.repositories.team import TeamMemberRepository
from src.multi_tenant.repositories.tenant import TenantRepository

REPOSITORIES = {
    schema.COLLECTION_CONVERSATIONS: ConversationRepository,
    schema.COLLECTION_CUSTOMER_PROFILES: CustomerProfileRepository,
    schema.COLLECTION_KNOWLEDGE_BASES: KnowledgeBaseRepository,
    schema.COLLECTION_MEMORY_VECTORS: MemoryVectorRepository,
    schema.COLLECTION_PREFERENCES: PreferencesRepository,
    schema.COLLECTION_TEAM_MEMBERS: TeamMemberRepository,
    schema.COLLECTION_TENANTS: TenantRepository,
}
JOBS = ["run_data_encryption_migration", "run_dek_rotation", "run_encryption_monitor"]


@pytest.mark.unit
@pytest.mark.parametrize("job", JOBS)
def test_the_job_lists_every_encrypted_field(job):
    lists = importlib.import_module(f"src.jobs.{job}").ENCRYPTED_COLLECTIONS
    expected = {name: set(repo._encryption_fields) for name, repo in REPOSITORIES.items()}
    actual = {name: set(fields) for name, fields in lists.items()}
    assert actual == expected


@pytest.mark.unit
@pytest.mark.parametrize("job", JOBS)
def test_the_job_covers_the_conversation_identity_fields(job):
    lists = importlib.import_module(f"src.jobs.{job}").ENCRYPTED_COLLECTIONS
    assert {"identity_email", "identity_phone"} <= set(lists[schema.COLLECTION_CONVERSATIONS])
