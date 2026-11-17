# Copyright (c) 2026 Michael Palmeter
# SPDX-License-Identifier: MIT
"""Export the module's read-only operator contract without starting services."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OPERATIONS = {"list_all_tenants_api_superadmin_tenants_get"}


def export_contract() -> tuple[dict, dict]:
    # Schema generation has no reason to resolve or connect to a host.
    def blocked(*args, **kwargs):
        raise RuntimeError("Network access is forbidden during contract generation")

    connect, resolve = socket.socket.connect, socket.getaddrinfo
    socket.socket.connect, socket.getaddrinfo = blocked, blocked
    try:
        from src.app.factory import create_app
        from src.app.routers import register_routers

        app = create_app()
        register_routers(app)
        full = app.openapi()
    finally:
        socket.socket.connect, socket.getaddrinfo = connect, resolve

    paths = {}
    for path, methods in full["paths"].items():
        for method, operation in methods.items():
            if isinstance(operation, dict) and operation.get("operationId") in OPERATIONS:
                operation = copy.deepcopy(operation)
                operation["operationId"] = "list_tenants"
                operation["security"] = [{"PlatformAdminKey": []}]
                operation["x-required-role"] = "platform_admin"
                paths.setdefault(path, {})[method] = operation
    if {op["operationId"] for methods in paths.values() for op in methods.values()} != {"list_tenants"}:
        raise ValueError("The core does not expose the reviewed tenant directory operation")
    needed = set()

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                needed.add(value["$ref"].removeprefix("#/components/schemas/"))
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)

    refs(paths)
    schemas = {}
    while needed - schemas.keys():
        for name in sorted(needed - schemas.keys()):
            schemas[name] = full["components"]["schemas"][name]
            refs(schemas[name])
    contract = {
        "openapi": full["openapi"],
        "info": {"title": "Agent Red operator read API", "version": "0.1.0"},
        "servers": [{"url": "https://core.example.invalid"}],
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {"PlatformAdminKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        },
    }
    return contract, full


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--full-output", type=Path)
    args = parser.parse_args()
    contract, full = export_contract()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # JSON is valid YAML, giving deterministic output without a runtime YAML dependency.
    args.output.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    if args.full_output:
        args.full_output.parent.mkdir(parents=True, exist_ok=True)
        args.full_output.write_text(json.dumps(full, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"module_operations": len(paths := contract["paths"]), "full_paths": len(full["paths"])}))
