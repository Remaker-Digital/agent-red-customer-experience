# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Explicit test-only guard inherited by the stdio subprocess."""

import os
import socket

if os.environ.get("AGENT_RED_TEST_BLOCK_NETWORK") == "1":
    _socket = socket.socket

    class NoInternetSocket(_socket):
        def __init__(self, family=socket.AF_INET, *args, **kwargs):
            if family in {socket.AF_INET, socket.AF_INET6}:
                raise RuntimeError("INET sockets are blocked in the verification subprocess")
            super().__init__(family, *args, **kwargs)

    def no_dns(*args, **kwargs):
        raise RuntimeError("DNS is blocked in the verification subprocess")

    socket.socket = NoInternetSocket
    socket.getaddrinfo = no_dns
