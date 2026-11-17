// Copyright (c) 2026 Michael Palmeter
// SPDX-License-Identifier: MIT
/**
 * Configuration suggestions hooks — fetch AI-generated config field suggestions.
 *
 * KA-7: Knowledge Automation Admin UI.
 *
 * Copyright (c) 2026 Michael Palmeter
 * SPDX-License-Identifier: MIT
 */

import { useApi } from './useApi';
import type { ApiFetch } from './useApi';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ConfigSuggestion {
  value: unknown;
  confidence: number;
  source: string;
}

export type SuggestionMap = Record<string, ConfigSuggestion>;

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/** Fetch configuration suggestions generated from KB content analysis. */
export function useConfigSuggestions(apiFetch: ApiFetch) {
  return useApi<SuggestionMap>(apiFetch, '/api/admin/knowledge/suggestions');
}
