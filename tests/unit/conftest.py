# © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.
"""Skip unit tests that need files kept out of the public source.

The public repository holds the product source only. Deploy scripts, the
release pipeline, infrastructure code, internal runbooks and the Stripe
product catalog are not in it. The tests listed here check those files, so
each one is skipped with the path it needs. A skip applies only while that
path is missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REASON = "needs {path}, which is not in the public source"

# Modules that cannot be imported without the path.
_MODULES: dict[str, str] = {
    "test_deploy_ui.py": "scripts/deploy_ui.py",
    "test_lib_scaling_enforcement.py": "scripts/lib/scaling_enforcement.py",
    "test_refresh_test_credentials.py": "scripts/refresh_test_credentials.py",
    "test_test_pipeline_junit_fallback.py": "scripts/test_pipeline.py",
}

# Test functions, by module and by the path each one needs.
_TESTS: dict[str, dict[str, tuple[str, ...]]] = {
    "test_ado_pipeline.py": {
        "azure-pipelines.yml": (
            "test_build_runs_deploy_pipeline_build_only_on_windows_with_the_service_connection",
            "test_build_runs_only_from_the_release_tag",
            "test_coverage_gate_enforces_seventy_five_percent",
            "test_every_azure_task_names_the_service_connection_at_compile_time",
            "test_every_python_script_command_matches_its_argparse_definitions",
            "test_frontend_vitest_and_docs_quality_jobs_exist",
            "test_no_secret_literal_in_the_pipeline",
            "test_nonfunctional_gates_match_gha_lint_and_security",
            "test_parameters",
            "test_playwright_ui_job_covers_mocked_spa_suites",
            "test_production_depends_on_staging_and_requires_deploy_production",
            "test_production_job_timeout_covers_the_full_profile_slow_path",
            "test_production_requires_every_full_profile_app_on_the_release_tag_after_deploying",
            "test_production_requires_every_full_profile_image_before_deploying",
            "test_pytest_shards_cover_ci_safe_directories",
            "test_runs_only_by_hand_and_leaves_pull_requests_to_github_actions",
            "test_semgrep_in_the_pipeline_never_scans_owner_messages",
            "test_stages_in_order_with_their_dependencies",
            "test_staging_deploys_the_single_container_profile_on_request_only",
            "test_staging_requires_the_new_revision_ready_healthy_and_on_the_tag",
            "test_test_stage_jobs_cover_full_nonprod_matrix",
            "test_tests_gate_the_build_without_live_suites",
            "test_the_release_gate_tools_are_pinned",
            "test_the_version_is_validated_in_a_step",
            "test_variables",
        ),
        "scripts/deploy.py": (
            "test_mapped_variables_are_read_by_the_scripts_and_documented_in_the_runbook",
        ),
        "docs/operations/ADO-NONPROD-TEST-EXCLUSIONS.md": (
            "test_production_only_exclusions_document_exists_and_is_cited",
        ),
        "docs/operations/CLOSED-BETA-RUNBOOK.md": (
            "test_runbook_assigns_the_release_branch_and_keeps_the_tag",
            "test_runbook_containerapp_commands_never_print_the_whole_app",
            "test_runbook_removes_both_rate_limit_bypasses",
        ),
        ".github/workflows/security-scan.yml": (
            "test_semgrep_in_the_security_scan_workflow_never_scans_owner_messages",
        ),
        ".gtkb-app-isolation.json": (
            "test_the_application_registry_declares_the_pipeline",
        ),
    },
    "test_batch5_misc_specs.py": {
        "scripts/seed_tenant.py": (
            "test_seed_script_exists",
            "test_seed_script_has_kb_phase",
            "test_seed_script_imports_kb_module",
            "test_seed_script_loads_articles",
            "test_seed_script_tracks_article_count",
        ),
    },
    "test_billing_caller_scope.py": {
        "config/stripe_product_ids.json": (
            "test_pack_purchase_refuses_another_tenant",
            "test_usage_summary_at_the_callers_tier",
        ),
    },
    "test_deploy_pipeline_acr_build.py": {
        "scripts/deploy_pipeline.py": (
            "test_abac_registry_builds_with_caller_identity",
            "test_dry_run_runs_no_az",
            "test_failed_submit_fails",
            "test_missing_tag_after_succeeded_run_fails",
            "test_mode_query_and_run_polls_are_capped_well_inside_the_budget",
            "test_no_poll_starts_or_runs_past_the_deadline",
            "test_non_abac_registry_builds_without_source_auth_flag",
            "test_non_succeeded_run_fails_naming_the_run",
            "test_run_id_is_parsed_from_merged_warning_output",
            "test_run_still_going_after_budget_fails_naming_the_run",
            "test_submit_without_run_id_fails",
            "test_success_verifies_the_submitted_run",
            "test_tag_check_runs_after_the_run_succeeds",
            "test_the_submit_cannot_have_its_run_id_warning_suppressed",
            "test_unrelated_succeeded_latest_run_cannot_pass",
        ),
    },
    "test_deploy_pipeline_build_context.py": {
        "scripts/deploy_pipeline.py": (
            "test_build_context_fails_when_docs_missing",
            "test_build_context_includes_docs_site_docs",
        ),
    },
    "test_deploy_pipeline_build_only.py": {
        "scripts/deploy_pipeline.py": (
            "test_build_only_stops_after_phase_7",
            "test_dry_run_never_queries_az",
            "test_production_deploy_never_queries_single_container",
            "test_staging_proceeds_when_single_container_off",
            "test_staging_refused_when_single_container_or_unknown",
        ),
    },
    "test_deploy_pipeline_production.py": {
        "scripts/deploy_pipeline.py": (
            "test_both_import_from_deploy_config",
            "test_mocked_smoke_failure_cli_exits_one_with_rollback",
            "test_mocked_success_path_cli_exits_zero",
            "test_no_input_calls_in_deploy_pipeline",
            "test_phase_11_fails_and_sets_rollback_attempted_on_smoke_failure",
            "test_phase_11_fails_when_snapshot_missing",
            "test_phase_11_returns_pass_when_all_checks_succeed",
            "test_pipeline_fails_closed_when_rollback_capture_unavailable",
            "test_production_allowed_with_env_var",
            "test_production_allowed_with_flag",
            "test_production_blocked_without_approval",
            "test_rollback_attempted_when_smoke_fails_with_image",
            "test_rollback_image_stored_in_args_when_captured",
            "test_rollback_not_attempted_when_no_image_captured",
            "test_rollback_succeeded_false_when_health_fails_after_rollback",
            "test_rollback_succeeded_false_when_rollback_command_fails",
            "test_rollback_succeeded_true_when_health_passes_after_rollback",
            "test_version_is_required_argument",
        ),
        "scripts/deploy_config.py": (
            "test_get_current_image_returns_image_on_success",
            "test_get_current_image_returns_none_on_az_failure",
            "test_no_input_calls_in_deploy_config",
            "test_production_tenant_id_from_env_var",
        ),
    },
    "test_deploy_pipeline_root_check.py": {
        "scripts/deploy_pipeline.py": (
            "test_each_missing_marker_is_reported",
            "test_markers_are_files_this_repository_carries",
            "test_repository_root_passes_without_claude_md",
        ),
    },
    "test_deploy_pipeline_scaling.py": {
        "scripts/deploy_pipeline.py": (
            "test_phase_15_az_commands_match_smoke_path",
            "test_phase_15_drift_appears_in_final_summary_when_status_is_pass",
            "test_phase_15_dry_run_skips_runner",
            "test_phase_15_production_returns_pass_when_all_succeed",
            "test_phase_15_single_failure_returns_pass_with_drift_extra",
            "test_scaling_config_matches_terraform_via_pipeline_import",
        ),
    },
    "test_deploy_scaling.py": {
        "scripts/deploy.py": (
            "test_both_gateway_names_in_scaling_config",
            "test_enforce_all_scaling_partial_failure_continues",
            "test_enforce_all_scaling_production_covers_all_prod_apps",
            "test_enforce_all_scaling_staging_uses_staging_gateway",
            "test_enforce_one_constructs_correct_az_command",
            "test_enforce_one_handles_az_failure",
            "test_enforce_scaling_backcompat_production_gateway",
            "test_enforce_scaling_backcompat_unknown_app_no_op",
            "test_nats_excluded_from_scaling_config",
            "test_scaling_config_covers_all_deployed_apps",
            "test_scaling_config_matches_terraform_for_deploy_managed_apps",
        ),
    },
    "test_deploy_single_container_profile.py": {
        "scripts/deploy.py": (
            "test_default_smoke_is_unchanged",
            "test_default_smoke_still_fails_without_done",
            "test_deploy_container_default_command_is_unchanged",
            "test_full_profile_proceeds_when_the_variable_is_absent_or_off",
            "test_full_profile_refuses_staging_in_beta_or_on_query_failure",
            "test_min_replicas_accepts_only_zero_or_one",
            "test_min_replicas_is_refused_with_the_full_profile",
            "test_production_full_profile_never_queries_single_container_state",
            "test_profile_accepts_only_known_names",
            "test_refuses_production_before_any_az_call",
            "test_single_container_enabled_reads_the_gateway_flag",
            "test_single_container_fails_when_beta_scaling_cannot_be_enforced",
            "test_single_container_honours_strict_smoke_failure",
            "test_strict_smoke_fails_on_missing_validated",
            "test_strict_smoke_fails_on_retracted",
            "test_strict_smoke_fails_when_a_retraction_follows_a_validated_event",
            "test_strict_smoke_passes_on_validated",
            "test_strict_smoke_still_applies_the_default_checks",
            "test_touches_only_staging_gateway",
            "test_verify_all_containers_default_checks_every_app",
        ),
    },
    "test_release_gate.py": {
        "scripts/test_pipeline.py": (
            "test_env_vars_missing_returns_warn",
            "test_env_vars_present_returns_none",
            "test_phase_19_in_all_group",
            "test_phase_19_in_live_group",
            "test_phase_19_in_live_phases",
            "test_phase_19_in_phase_order_all",
        ),
        "scripts/release_pipeline.py": (
            "test_step5_dry_run_returns_fail",
            "test_step5_incomplete_sse_is_failure",
            "test_step5_missing_widget_key_is_failure",
        ),
    },
    "test_scripts_run_without_shell.py": {
        "scripts/deploy.py": (
            "test_a_missing_program_is_a_failed_result_not_an_exception",
        ),
        "scripts/deploy_ui.py": (
            "test_an_interpolated_value_cannot_start_another_command",
            "test_no_shell_is_started",
            "test_quoted_arguments_reach_the_program_unchanged",
        ),
        "scripts/build.py": (
            "test_build_frontends_passes_project_paths_intact",
        ),
    },
    "test_widget_origins.py": {
        "terraform/scaling_profiles.tf": (
            "test_the_canary_job_definition_passes_every_setting_the_canary_reads",
        ),
    },
}


def _missing(path: str) -> bool:
    return not (_ROOT / path).exists()


class _SkippedModule(pytest.Module):
    """A test module whose import needs a path that is not here."""

    def collect(self):
        path = _MODULES[self.path.name]
        raise pytest.skip.Exception(_REASON.format(path=path), allow_module_level=True)


def pytest_pycollect_makemodule(module_path, parent):
    path = _MODULES.get(module_path.name)
    if path and _missing(path):
        return _SkippedModule.from_parent(parent, path=module_path)
    return None


def pytest_collection_modifyitems(config, items):
    for item in items:
        by_path = _TESTS.get(item.path.name)
        if not by_path:
            continue
        name = getattr(item, "originalname", None) or item.name.split("[")[0]
        for path, names in by_path.items():
            if name in names and _missing(path):
                item.add_marker(pytest.mark.skip(reason=_REASON.format(path=path)))
                break
