#!/usr/bin/env python3
"""Persistent, searchable network-interface inventory snapshots."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote

import yaml

import config
from config import mcp


SCHEMA_VERSION = 1
MAX_SEARCH_RESULTS = 1000
_INVENTORY_LOCKS: Dict[str, asyncio.Lock] = {}
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "passwd",
    "password",
    "secret",
    "token",
    "vnc_password",
}


def _inventory_root() -> Path:
    # MCP runners import ``main`` (which loads configuration), but direct tool
    # imports and in-process clients must resolve the same configured root.
    config._load_config()
    configured = os.getenv("NETWORK_INVENTORY_DIR") or config.SETTINGS.get(
        "network_inventory_dir", config.NETWORK_INVENTORY_DIR
    )
    return Path(os.path.expandvars(str(configured))).expanduser().resolve()


def _host_path(server_id: str) -> Path:
    filename = quote(server_id, safe="-_.") + ".yaml"
    return _inventory_root() / "hosts" / filename


def _inventory_lock(server_id: str) -> asyncio.Lock:
    return _INVENTORY_LOCKS.setdefault(server_id, asyncio.Lock())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _validate_timestamp(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("observed_at must be a non-empty timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    return value


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(_validate_timestamp(value).replace("Z", "+00:00"))


def _reject_secrets(value: Any, path: str = "inventory") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SENSITIVE_KEYS:
                raise ValueError(f"Sensitive field is not allowed in network inventory: {path}.{key}")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def _normalize_mac(value: str) -> str:
    compact = re.sub(r"[^0-9A-Fa-f]", "", value)
    if len(compact) != 12:
        raise ValueError(f"Invalid MAC address: {value}")
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2)).lower()


def _normalize_address(value: Any) -> Dict[str, Any]:
    entry = {"address": value} if isinstance(value, str) else dict(value)
    address = str(entry.get("address", "")).strip()
    if not address:
        raise ValueError("Every address needs a non-empty 'address' value")
    parsed = ipaddress.ip_interface(address)
    entry["address"] = str(parsed)
    family = str(entry.get("family") or f"ipv{parsed.version}").lower()
    aliases = {"inet": "ipv4", "inet6": "ipv6", "4": "ipv4", "6": "ipv6"}
    family = aliases.get(family, family)
    if family != f"ipv{parsed.version}":
        raise ValueError(f"Address family {family!r} does not match {address!r}")
    entry["family"] = family
    return entry


def _normalize_route(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Every route must be a mapping")
    route = dict(value)
    if route.get("raw") is not None:
        raw = str(route["raw"]).strip()
        if not raw:
            raise ValueError("Route 'raw' must not be empty")
        route["raw"] = raw
        return route

    destination = route.get("destination")
    if destination in (None, "default"):
        destination = "0.0.0.0/0"
    route["destination"] = str(ipaddress.ip_network(str(destination), strict=False))
    if route.get("gateway"):
        gateway = ipaddress.ip_address(str(route["gateway"]))
        if gateway.version != ipaddress.ip_network(route["destination"]).version:
            raise ValueError("Route gateway and destination use different address families")
        route["gateway"] = str(gateway)
    if route.get("interface") is not None:
        route["interface"] = str(route["interface"]).strip()
        if not route["interface"]:
            raise ValueError("Route interface must not be empty")
    return route


def _normalize_interface(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Every interface must be a mapping")
    interface = dict(value)
    name = str(interface.get("name", "")).strip()
    if not name:
        raise ValueError("Every interface needs a non-empty 'name'")
    interface["name"] = name

    if interface.get("mac_address"):
        interface["mac_address"] = _normalize_mac(str(interface["mac_address"]))

    addresses = interface.get("addresses", [])
    if not isinstance(addresses, list):
        raise ValueError(f"Interface {name}: 'addresses' must be a list")
    interface["addresses"] = [_normalize_address(address) for address in addresses]

    link = interface.get("link", {})
    if not isinstance(link, dict):
        raise ValueError(f"Interface {name}: 'link' must be a mapping")
    link = dict(link)
    if "detected" not in link or not isinstance(link["detected"], bool):
        raise ValueError(f"Interface {name}: link.detected must be a boolean")
    if link.get("speed_mbps") is not None:
        if isinstance(link["speed_mbps"], bool):
            raise ValueError(f"Interface {name}: link.speed_mbps must be a nonnegative integer")
        try:
            link["speed_mbps"] = int(link["speed_mbps"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Interface {name}: link.speed_mbps must be a nonnegative integer") from exc
        if link["speed_mbps"] < 0:
            raise ValueError(f"Interface {name}: link.speed_mbps must be a nonnegative integer")
    if link.get("state") is not None:
        link["state"] = str(link["state"]).strip().lower() or None
    interface["link"] = link
    return interface


def _load_inventory(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise ValueError("inventory document must be a mapping")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version: {value.get('schema_version')}")
    return _validate_loaded_document(value)


def _write_inventory(path: Path, inventory: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            yaml.safe_dump(inventory, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _normalize_server_id(server_id: str) -> str:
    if not isinstance(server_id, str) or not server_id.strip():
        raise ValueError("server_id is required")
    server_id = server_id.strip()
    if any(ord(character) < 32 for character in server_id):
        raise ValueError("server_id must not contain control characters")
    return server_id


def _build_inventory_document(
    server_id: str,
    interfaces: List[Dict[str, Any]],
    *,
    host: Optional[Dict[str, Any]] = None,
    routes: Optional[List[Dict[str, Any]]] = None,
    source: Optional[Dict[str, Any]] = None,
    observed_at: Optional[str] = None,
) -> Dict[str, Any]:
    server_id = _normalize_server_id(server_id)
    if not isinstance(interfaces, list):
        raise ValueError("interfaces must be a list")
    normalized = [_normalize_interface(interface) for interface in interfaces]
    names = [interface["name"] for interface in normalized]
    if len(names) != len(set(names)):
        raise ValueError("interface names must be unique within a host")
    document: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "server_id": server_id,
        "observed_at": _validate_timestamp(observed_at) if observed_at else _timestamp(),
    }
    if host:
        document["host"] = dict(host)
    if source:
        document["source"] = dict(source)
    document["interfaces"] = normalized
    document["routes"] = [_normalize_route(route) for route in (routes or [])]
    _reject_secrets(document)
    return document


def _validate_loaded_document(value: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-validate a stored schema document before consumers traverse it."""
    document = deepcopy(value)
    document["server_id"] = _normalize_server_id(document.get("server_id"))
    document["observed_at"] = _validate_timestamp(document.get("observed_at"))

    interfaces = document.get("interfaces")
    if not isinstance(interfaces, list):
        raise ValueError("inventory document must contain an interfaces list")
    normalized_interfaces: List[Dict[str, Any]] = []
    for index, interface in enumerate(interfaces):
        try:
            normalized_interfaces.append(_normalize_interface(interface))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"interfaces[{index}]: {exc}") from exc
    names = [interface["name"] for interface in normalized_interfaces]
    if len(names) != len(set(names)):
        raise ValueError("interface names must be unique within a host")
    document["interfaces"] = normalized_interfaces

    routes = document.get("routes", [])
    if not isinstance(routes, list):
        raise ValueError("inventory document routes must be a list")
    normalized_routes: List[Dict[str, Any]] = []
    for index, route in enumerate(routes):
        try:
            normalized_routes.append(_normalize_route(route))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"routes[{index}]: {exc}") from exc
    document["routes"] = normalized_routes

    for field in ("host", "source"):
        if field in document and document[field] is not None and not isinstance(document[field], dict):
            raise ValueError(f"inventory document {field} must be a mapping")
    _reject_secrets(document)
    return document


def _summarize_document(document: Dict[str, Any]) -> Dict[str, Any]:
    interfaces = document["interfaces"]
    return {
        "server_id": document["server_id"],
        "observed_at": document.get("observed_at"),
        "interface_count": len(interfaces),
        "links_up": sum(interface.get("link", {}).get("detected") is True for interface in interfaces),
        "address_count": sum(len(interface.get("addresses", [])) for interface in interfaces),
    }


def _iter_inventories() -> Iterable[tuple[Path, Dict[str, Any]]]:
    for path in sorted((_inventory_root() / "hosts").glob("*.yaml")):
        yield path, _load_inventory(path)


def _address_matches(address: str, query: str) -> bool:
    stored = ipaddress.ip_interface(address).ip
    if "/" in query:
        return stored in ipaddress.ip_network(query, strict=False)
    return stored == ipaddress.ip_address(query)


def _flat_match(inventory: Dict[str, Any], interface: Dict[str, Any]) -> Dict[str, Any]:
    link = interface.get("link", {})
    return {
        "server_id": inventory["server_id"],
        "observed_at": inventory.get("observed_at"),
        "name": interface["name"],
        "mac_address": interface.get("mac_address"),
        "vendor": interface.get("vendor"),
        "model": interface.get("model"),
        "pci_address": interface.get("pci_address"),
        "physical_port": interface.get("physical_port"),
        "driver": interface.get("driver"),
        "link_up": link.get("detected"),
        "link_state": link.get("state"),
        "speed_mbps": link.get("speed_mbps"),
        "media": link.get("media"),
        "addresses": interface.get("addresses", []),
    }


@mcp.tool(
    description=(
        "Save or replace one host's versioned network inventory as readable YAML. "
        "MAC and IP values are validated and normalized; no credentials are stored."
    )
)
async def save_network_inventory(
    server_id: str,
    interfaces: List[Dict[str, Any]],
    host: Optional[Dict[str, Any]] = None,
    routes: Optional[List[Dict[str, Any]]] = None,
    source: Optional[Dict[str, Any]] = None,
    observed_at: Optional[str] = None,
    reject_older: bool = True,
) -> Dict[str, Any]:
    """Persist the latest structured network snapshot for a host."""
    try:
        document = _build_inventory_document(
            server_id,
            interfaces,
            host=host,
            routes=routes,
            source=source,
            observed_at=observed_at,
        )
        server_id = document["server_id"]
        path = _host_path(server_id)
        async with _inventory_lock(server_id):
            operation = "updated" if path.exists() else "created"
            if reject_older and path.exists():
                existing = _load_inventory(path)
                if _parse_timestamp(document["observed_at"]) < _parse_timestamp(existing["observed_at"]):
                    return {
                        "status": "error",
                        "server_id": server_id,
                        "message": "refusing to replace a newer network inventory snapshot",
                        "existing_observed_at": existing["observed_at"],
                        "submitted_observed_at": document["observed_at"],
                    }
            _write_inventory(path, document)
    except (OSError, TypeError, ValueError) as exc:
        return {"status": "error", "server_id": server_id, "message": str(exc)}

    summary = _summarize_document(document)
    return {
        "status": "success",
        "operation": operation,
        "path": str(path),
        **summary,
    }


@mcp.tool(
    description=(
        "Validate and save multiple network inventory documents. Each host is serialized and older "
        "snapshots are rejected by default; results preserve input order."
    )
)
async def save_network_inventories(
    inventories: List[Dict[str, Any]],
    reject_older: bool = True,
) -> Dict[str, Any]:
    if not isinstance(inventories, list) or not inventories:
        return {"status": "error", "message": "inventories must be a non-empty list"}

    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in inventories:
        if not isinstance(item, dict):
            results.append({"status": "error", "message": "inventory entry must be a mapping"})
            continue
        server_id = str(item.get("server_id", "")).strip()
        if server_id in seen:
            results.append({"status": "error", "server_id": server_id, "message": "duplicate server_id in batch"})
            continue
        seen.add(server_id)
        results.append(
            await save_network_inventory(
                server_id=server_id,
                interfaces=item.get("interfaces"),
                host=item.get("host"),
                routes=item.get("routes"),
                source=item.get("source"),
                observed_at=item.get("observed_at"),
                reject_older=reject_older,
            )
        )

    failed = [result.get("server_id") for result in results if result.get("status") != "success"]
    return {
        "status": "success" if not failed else ("partial" if len(failed) < len(results) else "error"),
        "saved": len(results) - len(failed),
        "failed": failed,
        "results": results,
    }


@mcp.tool(description="Read the latest saved structured network inventory for one host.")
async def get_network_inventory(server_id: str) -> Dict[str, Any]:
    path = _host_path(server_id.strip())
    if not path.exists():
        return {"status": "error", "server_id": server_id, "message": "network inventory not found"}
    try:
        return {"status": "success", "inventory": _load_inventory(path), "path": str(path)}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"status": "error", "server_id": server_id, "message": str(exc)}


@mcp.tool(description="List saved host network inventories with interface, active-link, and address counts.")
async def list_network_inventories() -> Dict[str, Any]:
    hosts: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for path in sorted((_inventory_root() / "hosts").glob("*.yaml")):
        try:
            inventory = _load_inventory(path)
            hosts.append({**_summarize_document(inventory), "path": str(path)})
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append({"path": str(path), "message": str(exc)})
    return {
        "status": "partial" if errors else "success",
        "count": len(hosts),
        "hosts": hosts,
        "errors": errors,
    }


@mcp.tool(
    description=(
        "Search saved network inventories by exact MAC, interface-name substring, link state, "
        "IP address or subnet, vendor substring, PCI address, and/or server id."
    )
)
async def search_network_inventory(
    mac: Optional[str] = None,
    interface: Optional[str] = None,
    link_up: Optional[bool] = None,
    ip: Optional[str] = None,
    vendor: Optional[str] = None,
    pci_address: Optional[str] = None,
    server_id: Optional[str] = None,
    offset: int = 0,
    limit: int = 100,
) -> Dict[str, Any]:
    try:
        offset = int(offset)
        limit = int(limit)
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        if not 1 <= limit <= MAX_SEARCH_RESULTS:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
        normalized_mac = _normalize_mac(mac) if mac else None
        if ip:
            if "/" in ip:
                ipaddress.ip_network(ip, strict=False)
            else:
                ipaddress.ip_address(ip)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    matches: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for path in sorted((_inventory_root() / "hosts").glob("*.yaml")):
        try:
            inventory = _load_inventory(path)
            if server_id and inventory.get("server_id") != server_id:
                continue
            for item in inventory["interfaces"]:
                link = item.get("link", {})
                if normalized_mac and item.get("mac_address") != normalized_mac:
                    continue
                if interface and interface.lower() not in item.get("name", "").lower():
                    continue
                if link_up is not None and link.get("detected") is not link_up:
                    continue
                if vendor and vendor.lower() not in str(item.get("vendor", "")).lower():
                    continue
                if pci_address and pci_address.lower() != str(item.get("pci_address", "")).lower():
                    continue
                if ip and not any(_address_matches(address["address"], ip) for address in item.get("addresses", [])):
                    continue
                matches.append(_flat_match(inventory, item))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append({"path": str(path), "message": str(exc)})

    total_count = len(matches)
    page = matches[offset:offset + limit]
    return {
        "status": "partial" if errors else "success",
        "count": len(page),
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total_count,
        "matches": page,
        "errors": errors,
    }


def _configured_host_macs(server_config: Dict[str, Any]) -> set[str]:
    values: List[Any] = []
    for key in ("expected_host_macs", "host_macs"):
        configured = server_config.get(key)
        if isinstance(configured, list):
            values.extend(configured)
    for key in ("expected_host_mac", "host_mac", "mac"):
        if server_config.get(key):
            values.append(server_config[key])
    return {_normalize_mac(str(value)) for value in values}


def _inventory_documents(
    server_ids: Optional[List[str]] = None,
) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    selected = set(server_ids or [])
    documents: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    for path in sorted((_inventory_root() / "hosts").glob("*.yaml")):
        try:
            inventory = _load_inventory(path)
            server_id = inventory.get("server_id")
            if selected and server_id not in selected:
                continue
            if server_id in documents:
                raise ValueError(f"duplicate saved inventory for {server_id}")
            documents[server_id] = inventory
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append({"path": str(path), "message": str(exc)})
    return documents, errors


def _build_aggregate(documents: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    connected: List[Dict[str, Any]] = []
    mac_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    ip_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    bmc_mac_index: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for server_id in sorted(documents):
        document = documents[server_id]
        for interface in document["interfaces"]:
            link = interface.get("link", {})
            owner = {
                "host": server_id,
                "interface": interface["name"],
                "link_detected": link.get("detected"),
            }
            mac = interface.get("mac_address")
            if mac:
                mac_index[mac].append(owner)
            for address in interface.get("addresses", []):
                ip_index[address["address"]].append(
                    {
                        "host": server_id,
                        "interface": interface["name"],
                        "family": address.get("family"),
                        "scope": address.get("scope"),
                    }
                )
            if link.get("detected") is True:
                connected.append(
                    {
                        "host": server_id,
                        "interface": interface["name"],
                        "mac_address": mac,
                        "vendor": interface.get("vendor"),
                        "model": interface.get("model"),
                        "pci_address": interface.get("pci_address"),
                        "physical_port": interface.get("physical_port"),
                        "speed_mbps": link.get("speed_mbps"),
                        "addresses": deepcopy(interface.get("addresses", [])),
                    }
                )

        host = document.get("host") or {}
        bmc_mac = host.get("bmc_mac_address") or host.get("bmc_mac")
        if bmc_mac:
            normalized = _normalize_mac(str(bmc_mac))
            bmc_mac_index[normalized].append(
                {
                    "host": server_id,
                    "hostname": host.get("bmc_hostname"),
                    "address": host.get("bmc_address"),
                }
            )

    duplicate_macs = {mac: owners for mac, owners in mac_index.items() if len({owner["host"] for owner in owners}) > 1}
    return {
        "schema_version": SCHEMA_VERSION,
        "inventory": "network",
        "generated_at": _timestamp(),
        "summary": {
            "hosts_total": len(documents),
            "physical_interfaces": sum(len(document["interfaces"]) for document in documents.values()),
            "connected_interfaces": len(connected),
            "addresses": sum(
                len(interface.get("addresses", []))
                for document in documents.values()
                for interface in document["interfaces"]
            ),
            "duplicate_host_macs": len(duplicate_macs),
        },
        "connected_interfaces": connected,
        "mac_index": dict(sorted(mac_index.items())),
        "ip_index": dict(sorted(ip_index.items())),
        "bmc_mac_index": dict(sorted(bmc_mac_index.items())),
        "duplicate_macs": duplicate_macs,
        "hosts": {server_id: deepcopy(documents[server_id]) for server_id in sorted(documents)},
    }


@mcp.tool(
    description=(
        "Validate saved network snapshots for coverage, age, duplicate MAC ownership, missing "
        "connected-interface MACs, and configured host-MAC identity mismatches."
    )
)
async def validate_network_inventories(
    server_ids: Optional[List[str]] = None,
    max_age_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    if server_ids is not None:
        if not isinstance(server_ids, list) or not server_ids:
            return {"status": "error", "message": "server_ids must be a non-empty list when provided"}
        requested = list(dict.fromkeys(str(server_id).strip() for server_id in server_ids))
    else:
        config._load_config()
        requested = sorted(config.CONFIG) if config.CONFIG else []

    if max_age_seconds is not None:
        try:
            max_age_seconds = int(max_age_seconds)
        except (TypeError, ValueError):
            return {"status": "error", "message": "max_age_seconds must be an integer"}
        if max_age_seconds < 0:
            return {"status": "error", "message": "max_age_seconds must be nonnegative"}

    documents, malformed = _inventory_documents(requested or None)
    if not requested:
        requested = sorted(documents)
    missing = sorted(set(requested) - set(documents))
    stale: List[Dict[str, Any]] = []
    up_without_mac: List[Dict[str, str]] = []
    empty: List[str] = []
    identity_mismatches: List[Dict[str, Any]] = []
    mac_owners: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    now = datetime.now(timezone.utc)

    for server_id, document in documents.items():
        interfaces = document["interfaces"]
        if not interfaces:
            empty.append(server_id)
        if max_age_seconds is not None:
            age = max(0, int((now - _parse_timestamp(document["observed_at"])).total_seconds()))
            if age > max_age_seconds:
                stale.append({"server_id": server_id, "age_seconds": age})
        observed_macs: set[str] = set()
        for interface in interfaces:
            mac = interface.get("mac_address")
            if mac:
                observed_macs.add(mac)
                mac_owners[mac].append({"host": server_id, "interface": interface["name"]})
            elif interface.get("link", {}).get("detected") is True:
                up_without_mac.append({"host": server_id, "interface": interface["name"]})

        server_config = config.CONFIG.get(server_id, {})
        try:
            expected_macs = _configured_host_macs(server_config)
        except ValueError as exc:
            identity_mismatches.append(
                {"server_id": server_id, "reason": "invalid configured host MAC", "message": str(exc)}
            )
        else:
            if expected_macs and not expected_macs.issubset(observed_macs):
                identity_mismatches.append(
                    {
                        "server_id": server_id,
                        "expected_host_macs": sorted(expected_macs),
                        "missing_host_macs": sorted(expected_macs - observed_macs),
                    }
                )

    duplicate_macs = {
        mac: owners for mac, owners in sorted(mac_owners.items()) if len({owner["host"] for owner in owners}) > 1
    }
    issues = {
        "missing": missing,
        "malformed": malformed,
        "stale": stale,
        "empty": sorted(empty),
        "duplicate_macs": duplicate_macs,
        "up_without_mac": up_without_mac,
        "identity_mismatches": identity_mismatches,
    }
    issue_count = sum(len(value) for value in issues.values())
    return {
        "status": "success" if issue_count == 0 else "partial",
        "checked": len(documents),
        "issue_count": issue_count,
        **issues,
    }


@mcp.tool(
    description=(
        "Build one readable and machine-parseable aggregate network inventory with connected, MAC, "
        "IP, and separate BMC-MAC indexes. Output is atomically written below the configured root."
    )
)
async def export_network_inventory(
    server_ids: Optional[List[str]] = None,
    format: str = "yaml",
    collection: str = "network-inventory",
) -> Dict[str, Any]:
    if not isinstance(collection, str) or not _COLLECTION_RE.fullmatch(collection):
        return {
            "status": "error",
            "message": "collection must contain only letters, numbers, dot, underscore, or dash",
        }
    format = str(format).strip().lower()
    if format not in {"yaml", "json"}:
        return {"status": "error", "message": "format must be 'yaml' or 'json'"}
    if server_ids is not None and (not isinstance(server_ids, list) or not server_ids):
        return {"status": "error", "message": "server_ids must be a non-empty list when provided"}

    selected = list(dict.fromkeys(server_ids or []))
    documents, malformed = _inventory_documents(selected or None)
    missing = sorted(set(selected) - set(documents))
    if not documents:
        return {
            "status": "error",
            "message": "no valid saved network inventories matched",
            "missing": missing,
            "malformed": malformed,
        }

    try:
        aggregate = _build_aggregate(documents)
        extension = "yaml" if format == "yaml" else "json"
        path = (_inventory_root() / "exports" / f"{collection}.{extension}").resolve()
        export_root = (_inventory_root() / "exports").resolve()
        if export_root not in path.parents:
            raise ValueError("export path escapes configured inventory root")
        if format == "yaml":
            _write_inventory(path, aggregate)
        else:
            _write_json(path, aggregate)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return {"status": "error", "message": str(exc)}

    duplicate_macs = aggregate["duplicate_macs"]
    return {
        "status": "success" if not (missing or malformed or duplicate_macs) else "partial",
        "path": str(path),
        "format": format,
        **aggregate["summary"],
        "missing": missing,
        "malformed": malformed,
        "duplicate_macs": duplicate_macs,
    }
