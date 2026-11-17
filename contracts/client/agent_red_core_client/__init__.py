# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT

"""A client library for accessing Agent Red operator read API"""

from .client import AuthenticatedClient, Client

__all__ = (
    "AuthenticatedClient",
    "Client",
)
