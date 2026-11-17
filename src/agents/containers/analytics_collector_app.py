# Agent Red — Analytics Collector Container Entry Point
#
# Run: uvicorn src.agents.containers.analytics_collector_app:app --port 8085
#
# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT

from __future__ import annotations

from src.agents.analytics_collector import AnalyticsCollectorAgent
from src.agents.containers.agent_app import create_agent_app

app = create_agent_app(AnalyticsCollectorAgent)
