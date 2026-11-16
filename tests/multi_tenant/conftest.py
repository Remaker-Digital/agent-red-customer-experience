"""Shared fixtures and helpers for multi_tenant mutation tests.

Provides:
    - MutationTestBase mixin with common auth/validation assertions
    - Service wiring fixtures for superadmin, knowledge, team, etc.
    - CRUD lifecycle helpers

© 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pathlib import Path

import pytest

from src.multi_tenant.superadmin_api._monolith import (
    configure_copilot_knowledge_service,
    configure_superadmin_services,
)
from src.multi_tenant.admin_knowledge_api import configure_admin_knowledge_services
from src.multi_tenant.admin_apikey_api import configure_apikey_services
from src.multi_tenant.admin_team_api import configure_admin_team_services
from src.multi_tenant.admin_conversation_api import configure_admin_conversation_services
from src.multi_tenant.admin_quick_action_api import configure_admin_quick_action_services
from src.multi_tenant.memory_dashboard import configure_memory_dashboard


# ---------------------------------------------------------------------------
# MutationTestBase — shared assertion helpers
# ---------------------------------------------------------------------------


class MutationTestBase:
    """Mixin providing common mutation endpoint assertions.

    Subclass this alongside unittest.TestCase or use as a mixin with
    pytest test classes. All methods accept an http method name (string)
    and a URL, plus optional json body.
    """

    @staticmethod
    def assert_requires_auth(raw_client, method: str, url: str, **kwargs):
        """Verify endpoint returns 401 without authentication."""
        resp = getattr(raw_client, method)(url, **kwargs)
        assert resp.status_code == 401, (
            f"Expected 401 for unauthenticated {method.upper()} {url}, "
            f"got {resp.status_code}"
        )

    @staticmethod
    def assert_rejects_widget_key(widget_client, method: str, url: str, **kwargs):
        """Verify admin endpoint rejects widget key auth.

        Widget keys are only valid for /api/chat/* and /api/config.
        Admin endpoints should return 401 (path not allowed for widget keys).
        """
        resp = getattr(widget_client, method)(url, **kwargs)
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for widget key on {method.upper()} {url}, "
            f"got {resp.status_code}"
        )

    @staticmethod
    def assert_spa_isolation(tenant_client, method: str, url: str, **kwargs):
        """Verify superadmin endpoint returns 403 for tenant keys."""
        resp = getattr(tenant_client, method)(url, **kwargs)
        assert resp.status_code == 403, (
            f"Expected 403 for tenant key on superadmin {method.upper()} {url}, "
            f"got {resp.status_code}"
        )

    @staticmethod
    def assert_validation_error(client, method: str, url: str, **kwargs):
        """Verify malformed input returns 422."""
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 422, (
            f"Expected 422 for invalid input on {method.upper()} {url}, "
            f"got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Mock repository factory helpers
# ---------------------------------------------------------------------------


def make_mock_repo(**overrides) -> AsyncMock:
    """Create a mock repository with common async methods."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.create = AsyncMock(return_value={"id": "mock-id"})
    repo.update = AsyncMock(return_value={"id": "mock-id"})
    repo.delete = AsyncMock(return_value=None)
    repo.list = AsyncMock(return_value=[])
    repo.query = AsyncMock(return_value=[])
    for key, value in overrides.items():
        setattr(repo, key, value)
    return repo


# ---------------------------------------------------------------------------
# Superadmin service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def superadmin_repos():
    """Pre-configured mock repos for all superadmin domain modules.

    Wires configure_superadmin_services() with mock repos and yields
    a dict of all mocks for test assertions.
    """
    repos = {
        "tenant_repo": make_mock_repo(),
        "audit_repo": make_mock_repo(),
        "conv_repo": make_mock_repo(),
        "usage_repo": make_mock_repo(),
        "prefs_repo": make_mock_repo(),
        "nats_mgr": MagicMock(),
        "secret_service": MagicMock(),
        "incident_repo": make_mock_repo(),
        "alert_rule_repo": make_mock_repo(),
        "alert_history_repo": make_mock_repo(),
        "platform_admin_repo": make_mock_repo(),
    }
    configure_superadmin_services(**repos)
    yield repos
    # Cleanup — reset all module-level refs to None
    configure_superadmin_services(
        tenant_repo=None,
        audit_repo=None,
    )


@pytest.fixture
def copilot_repos():
    """Mock repo for Co-Pilot knowledge management."""
    repo = make_mock_repo()
    configure_copilot_knowledge_service(admin_doc_repo=repo)
    yield {"admin_doc_repo": repo}
    configure_copilot_knowledge_service(admin_doc_repo=None)


# ---------------------------------------------------------------------------
# Tenant admin service fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def knowledge_repos():
    """Mock repos for admin knowledge API."""
    repo = make_mock_repo()
    vectorizer = AsyncMock()
    staleness = AsyncMock()
    scanner = AsyncMock()
    configure_admin_knowledge_services(
        knowledge_repo=repo,
        knowledge_vectorizer=vectorizer,
        staleness_service=staleness,
        conflict_scanner=scanner,
    )
    yield {
        "knowledge_repo": repo,
        "knowledge_vectorizer": vectorizer,
        "staleness_service": staleness,
        "conflict_scanner": scanner,
    }
    configure_admin_knowledge_services(knowledge_repo=None)


@pytest.fixture
def apikey_repos():
    """Mock repos for admin API key endpoints."""
    tenant_repo = make_mock_repo()
    audit_repo = make_mock_repo()
    configure_apikey_services(tenant_repo=tenant_repo, audit_repo=audit_repo)
    yield {"tenant_repo": tenant_repo, "audit_repo": audit_repo}
    configure_apikey_services(tenant_repo=None)


@pytest.fixture
def team_repos():
    """Mock repos for admin team API."""
    team_repo = make_mock_repo()
    audit_repo = make_mock_repo()
    conv_repo = make_mock_repo()
    configure_admin_team_services(
        team_repo=team_repo,
        audit_repo=audit_repo,
        conv_repo=conv_repo,
    )
    yield {
        "team_repo": team_repo,
        "audit_repo": audit_repo,
        "conv_repo": conv_repo,
    }
    configure_admin_team_services(team_repo=None)


@pytest.fixture
def conversation_repos():
    """Mock repos for admin conversation API."""
    conv_repo = make_mock_repo()
    configure_admin_conversation_services(conversation_repo=conv_repo)
    yield {"conversation_repo": conv_repo}
    configure_admin_conversation_services(conversation_repo=None)


@pytest.fixture
def quick_action_repos():
    """Mock repos for admin quick action API."""
    prefs_repo = make_mock_repo()
    configure_admin_quick_action_services(prefs_repo=prefs_repo)
    yield {"prefs_repo": prefs_repo}
    configure_admin_quick_action_services(prefs_repo=None)


@pytest.fixture
def memory_repos():
    """Mock repos for memory dashboard API."""
    memory_repo = make_mock_repo()
    configure_memory_dashboard(memory_repo=memory_repo)
    yield {"memory_repo": memory_repo}
    configure_memory_dashboard(memory_repo=None)


_PUBLIC_SNAPSHOT_ROOT = Path(__file__).resolve().parents[2]
_PRIVATE_PATHS = {
    "test_s153_batch10_spec_verification.py": {
        ".claude/SCHEDULE.md": {
            "test_schedule_exists",
            "test_schedule_has_groups",
        },
        "CLAUDE.md": {
            "test_honest_feedback_in_project",
            "test_customer_experience_in_claude_md",
            "test_priority_in_claude_md",
            "test_no_effort_estimates_as_metric",
            "test_quality_governance",
            "test_no_effort_in_governance",
            "test_quality_over_effort",
            "test_feedback_guidance",
            "test_work_priority_bias",
            "test_quality_governance_present",
            "test_honest_feedback_documented",
        },
        "CLAUDE_ARCHIVE.md": {
            "test_archive_exists",
            "test_claude_md_references_archive",
        },
        "scripts/deploy_pipeline.py": {
            "test_deploy_pipeline_has_phases",
        },
        "scripts/run-tests-thermal-safe.ps1": {
            "test_thermal_safe_script",
            "test_cooling_cycles",
            "test_test_harness_exists",
            "test_harness_runs_multiple_suites",
        },
        "scripts/seed_tenant.py": {
            "test_seed_script_cleans_data",
        },
        "scripts/test_pipeline.py": {
            "test_test_pipeline_exists",
            "test_test_pipeline_has_phases",
        },
    },
    "test_s153_batch11_spec_verification.py": {
        ".claude/SCHEDULE.md": {
            "test_schedule_is_writable",
        },
        ".claude/hooks/assertion-check.py": {
            "test_quality_dashboard_exists",
        },
        "CLAUDE.md": {
            "test_deploy_gate_in_governance",
            "test_quality_first_in_governance",
            "test_gov_03_test_clarity",
        },
        "scripts/archive/record_test_results.py": {
            "test_record_test_results_script",
            "test_script_parses_junit",
        },
        "scripts/deploy_pipeline.py": {
            "test_staging_environment_configured",
            "test_deploy_pipeline_comprehensive",
            "test_production_verification_phase",
            "test_production_track_in_pipeline",
            "test_deploy_pipeline_error_handling",
        },
        "scripts/seed_tenant.py": {
            "test_seed_script_creates_clean_state",
            "test_seed_is_distinct_script",
            "test_seed_has_multiple_phases",
            "test_seed_creates_example_data",
            "test_seed_deletes_all_prior_data",
            "test_seed_creates_realistic_data",
        },
        "scripts/upgrade_verification.py": {
            "test_upgrade_verification_exists",
            "test_upgrade_phases",
            "test_upgrade_verification_script",
        },
    },
    "test_s153_batch12_spec_verification.py": {
        "scripts/deploy_pipeline.py": {
            "test_deploy_pipeline_has_verification",
            "test_deploy_pipeline_exists",
            "test_deploy_pipeline_has_phases",
            "test_deploy_pipeline_has_staging_and_production",
        },
        "scripts/test_pipeline.py": {
            "test_test_pipeline_exists",
            "test_test_pipeline_has_phases",
        },
        "scripts/upgrade_verification.py": {
            "test_upgrade_verification_exists",
        },
    },
    "test_s153_batch4_spec_verification.py": {
        ".claude/hooks/assertion-check.py": {
            "test_assertion_check_hook_has_dashboard",
        },
    },
    "test_s153_batch5_spec_verification.py": {
        "CLAUDE.md": {
            "test_agntcy_referenced_as_external",
        },
        "scripts/": {
            "test_staging_config_exists",
        },
        "scripts/upgrade_verification.py": {
            "test_upgrade_script_has_env_param",
        },
    },
    "test_s153_batch7_spec_verification.py": {
        "scripts/deploy_pipeline.py": {
            "test_deploy_pipeline_exists",
            "test_deploy_pipeline_automated",
            "test_staging_track_exists",
            "test_rollback_documented",
            "test_staging_before_production",
            "test_staging_deployment",
            "test_staging_validation_first",
        },
        "scripts/upgrade_verification.py": {
            "test_upgrade_verifies_keys",
            "test_upgrade_verification_exists",
            "test_data_integrity_assertions",
            "test_team_data_verification",
        },
    },
    "test_s153_batch9_spec_verification.py": {
        "CLAUDE.md": {
            "test_glossary_in_project",
        },
        "scripts/seed_tenant.py": {
            "test_seed_script_exists",
            "test_seed_before_tests",
        },
    },
}


def pytest_collection_modifyitems(items):
    for item in items:
        paths = _PRIVATE_PATHS.get(item.path.name, {})
        name = item.originalname if hasattr(item, "originalname") else item.name
        for path, tests in paths.items():
            if name in tests and not (_PUBLIC_SNAPSHOT_ROOT / path).exists():
                item.add_marker(pytest.mark.skip(reason=f"needs {path}, which is not in the public source"))
                break
