# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""
Shared Pydantic base model for API DTOs.

All API response/request models that use camelCase JSON serialization
should inherit from CamelCaseModel instead of BaseModel directly.
This eliminates repeated ConfigDict boilerplate across API modules.

Copyright (c) 2026 Michael Palmeter
SPDX-License-Identifier: MIT
"""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelCaseModel(BaseModel):
    """Base model for API DTOs with camelCase alias generation."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
