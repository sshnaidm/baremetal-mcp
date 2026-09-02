#!/usr/bin/env python3
"""
Low-level Redfish API tools - direct API access.
"""

import asyncio
import secrets
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

import config as cfg
from config import mcp
from helpers import _origin_relative_path, _redfish_call

_READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SUPPORTED_METHODS = _READ_ONLY_METHODS | _MUTATING_METHODS
_MAX_BATCH_SIZE = 64
_MAX_CONCURRENCY = 12


def _is_mutating_method(method: Any) -> bool:
    return isinstance(method, str) and method.upper() in _MUTATING_METHODS


def _not_sent_result(result: Dict[str, Any], method: Any) -> Dict[str, Any]:
    """Annotate a public mutation that validation/confirmation kept local."""
    value = dict(result)
    if _is_mutating_method(method):
        value.setdefault("remote_request_sent", False)
        value.setdefault("retry_safe", True)
        value.setdefault("outcome_unknown", False)
    return value


def _conservative_result(result: Dict[str, Any], method: Any) -> Dict[str, Any]:
    """Never imply a failed public mutation is safe to repeat without evidence."""
    value = dict(result)
    if _is_mutating_method(method) and value.get("status") != "success":
        value.setdefault("remote_request_sent", None)
        value.setdefault("retry_safe", False)
        value.setdefault("outcome_unknown", True)
    return value


def _payload_error(payload: Any) -> Optional[Dict[str, Any]]:
    if payload is not None and not isinstance(payload, Mapping):
        return {
            "status": "error",
            "phase": "validation",
            "message": "payload must be a JSON object when provided",
        }
    return None


def _prepare_public_call(
    method: str,
    path: str,
    dry_run: bool,
    confirm_method_path: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Validate a public passthrough call and guard every mutation."""
    if not isinstance(dry_run, bool):
        return (
            None,
            None,
            {
                "status": "error",
                "phase": "validation",
                "message": "dry_run must be true or false",
            },
        )
    if not isinstance(method, str) or not method or method != method.strip():
        return (
            None,
            None,
            {
                "status": "error",
                "phase": "validation",
                "message": "method must be GET, HEAD, OPTIONS, POST, PUT, PATCH, or DELETE",
            },
        )
    method_name = method.upper()
    if method_name not in _SUPPORTED_METHODS:
        return (
            None,
            None,
            {
                "status": "error",
                "phase": "validation",
                "message": "method must be GET, HEAD, OPTIONS, POST, PUT, PATCH, or DELETE",
            },
        )
    try:
        normalized_path = _origin_relative_path(path)
    except ValueError as exc:
        return (
            None,
            None,
            {
                "status": "error",
                "phase": "validation",
                "message": str(exc),
            },
        )

    if method_name in _MUTATING_METHODS:
        required_confirmation = f"{method_name} {normalized_path}"
        confirmed = isinstance(confirm_method_path, str) and secrets.compare_digest(
            confirm_method_path,
            required_confirmation,
        )
        if dry_run or not confirmed:
            return (
                method_name,
                normalized_path,
                {
                    "status": "error",
                    "phase": "confirmation",
                    "dry_run": True,
                    "requested_dry_run": dry_run,
                    "remote_request_sent": False,
                    "retry_safe": True,
                    "outcome_unknown": False,
                    "method": method_name,
                    "path": normalized_path,
                    "required_confirmation": required_confirmation,
                    "message": (
                        "Mutating Redfish request was not sent. Set dry_run=false and "
                        "confirm_method_path to the exact required_confirmation value, or use a "
                        "declarative high-level tool."
                    ),
                },
            )
    return method_name, normalized_path, None


def _normalize_server_ids(
    server_ids: List[str],
) -> Tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
    if not isinstance(server_ids, list) or not server_ids:
        return None, {
            "status": "error",
            "phase": "validation",
            "message": "server_ids must be a non-empty list",
        }
    if len(server_ids) > _MAX_BATCH_SIZE:
        return None, {
            "status": "error",
            "phase": "validation",
            "message": f"At most {_MAX_BATCH_SIZE} server IDs may be requested at once",
        }

    normalized: List[str] = []
    seen = set()
    for value in server_ids:
        if not isinstance(value, str) or not value.strip():
            return None, {
                "status": "error",
                "phase": "validation",
                "message": "Every server_id must be a non-empty string",
            }
        server_id = value.strip()
        if len(server_id) > 128 or any(not 32 <= ord(char) <= 126 for char in server_id):
            return None, {
                "status": "error",
                "phase": "validation",
                "message": "Invalid server_id",
            }
        if server_id not in seen:
            seen.add(server_id)
            normalized.append(server_id)
    return normalized, None


def _parallel_concurrency(value: Optional[int]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    concurrency = getattr(cfg, "BATCH_CONCURRENCY", 6) if value is None else value
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= _MAX_CONCURRENCY:
        return None, {
            "status": "error",
            "phase": "validation",
            "message": f"concurrency must be an integer between 1 and {_MAX_CONCURRENCY}",
        }
    return concurrency, None


@mcp.tool(
    description=(
        "Low-level same-BMC Redfish call for a single server. Path must be origin-relative; "
        "absolute and network-path URLs are rejected. GET/HEAD/OPTIONS are read-only; "
        "POST/PUT/PATCH/DELETE default to dry-run and require dry_run=false plus "
        "confirm_method_path exactly matching 'METHOD /path'."
    )
)
async def redfish_call(
    server_id: str,
    method: str,
    path: str,
    payload: Optional[Dict] = None,
    dry_run: bool = True,
    confirm_method_path: Optional[str] = None,
) -> Dict:
    """Make a low-level Redfish API call.

    Args
    - server_id: Target host id.
    - method: HTTP method (GET, POST, PATCH, DELETE).
    - path: Origin-relative Redfish resource path (e.g., /redfish/v1/Systems/1).
    - payload: Optional JSON body for POST/PATCH.
    - dry_run: Ignored for reads. Mutations are refused unless explicitly false.
    - confirm_method_path: For a mutation, the exact uppercase method and normalized path,
      separated by one space (e.g., POST /redfish/v1/Systems/1/Actions/Reset).

    Returns (success)
    {"status": "success", "data": <json-or-bytes>, "headers": { ... }}

    Returns (error)
    {"status": "error", "message": "..."}
    """
    invalid_payload = _payload_error(payload)
    if invalid_payload:
        return _not_sent_result(invalid_payload, method)
    method_name, normalized_path, error = _prepare_public_call(
        method,
        path,
        dry_run,
        confirm_method_path,
    )
    if error:
        return _not_sent_result(error, method)
    result = await _redfish_call(
        server_id,
        method_name,
        normalized_path,
        dict(payload) if payload is not None else None,
    )
    return _conservative_result(result, method_name)


@mcp.tool(
    description=(
        "Low-level same-BMC Redfish call over multiple servers in parallel. Path must be "
        "origin-relative; hosts are deduplicated and concurrency is bounded. Mutations require "
        "dry_run=false and confirm_method_path exactly matching 'METHOD /path'. Adds server_id "
        "to each result."
    )
)
async def parallel_redfish_call(
    server_ids: List[str],
    method: str,
    path: str,
    payload: Optional[Dict] = None,
    dry_run: bool = True,
    confirm_method_path: Optional[str] = None,
    concurrency: Optional[int] = None,
) -> List[Dict]:
    """Make the same Redfish call across many servers in parallel.

    Args
    - server_ids: List of host ids.
    - method/path/payload: Same as redfish_call.
    - dry_run: Ignored for reads. Mutations are refused unless explicitly false.
    - confirm_method_path: Exact mutation confirmation, as for redfish_call.
    - concurrency: Maximum simultaneous requests (1-12; configured batch default).

    Returns
    - List of per-server results, each including server_id.
    """
    normalized_ids, ids_error = _normalize_server_ids(server_ids)
    if ids_error:
        return [_not_sent_result(ids_error, method)]
    invalid_payload = _payload_error(payload)
    if invalid_payload:
        error = _not_sent_result(invalid_payload, method)
        return [{"server_id": server_id, **error} for server_id in normalized_ids]
    limit, concurrency_error = _parallel_concurrency(concurrency)
    if concurrency_error:
        return [_not_sent_result(concurrency_error, method)]
    method_name, normalized_path, call_error = _prepare_public_call(
        method,
        path,
        dry_run,
        confirm_method_path,
    )
    if call_error:
        error = _not_sent_result(call_error, method)
        return [{"server_id": server_id, **error} for server_id in normalized_ids]

    semaphore = asyncio.Semaphore(limit)

    async def run(server_id: str) -> Dict:
        async with semaphore:
            result = await _redfish_call(
                server_id,
                method_name,
                normalized_path,
                dict(payload) if payload is not None else None,
            )
            return {"server_id": server_id, **_conservative_result(result, method_name)}

    results = await asyncio.gather(*(run(server_id) for server_id in normalized_ids))
    return results
