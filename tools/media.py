"""
Virtual media management tools - mount/unmount ISO images.
"""

import asyncio
import time
from urllib.parse import urlparse

import config as cfg
from config import mcp
from helpers import (
    _eject_virtual_media,
    _ensure_boot_once_single,
    _get_handler,
    _get_vm_path_and_state,
    _insert_virtual_media,
    _redfish_call,
)

_MAX_BATCH_SIZE = 64
_MAX_CONCURRENCY = 12


def _mutation_record(action: str, phase: str, result: dict, **details: object) -> dict:
    """Return a small, stable record for one mutating Redfish request."""
    sent = result.get("remote_request_sent")
    accepted = result.get("status") == "success"
    unknown = bool(result.get("outcome_unknown"))
    if accepted:
        state = "accepted"
        sent = True
        unknown = False
    elif unknown or sent is None:
        state = "sent_unconfirmed"
    elif sent is True:
        state = "rejected"
    else:
        state = "not_sent"
    return {
        "action": action,
        "phase": phase,
        "state": state,
        "remote_request_sent": sent,
        "outcome_unknown": unknown,
        "retry_safe": bool(result.get("retry_safe", sent is False)),
        **details,
    }


def _ambiguous_mutation(action: str, phase: str, **details: object) -> dict:
    """Conservatively describe an exception raised during a mutation call."""
    return {
        "action": action,
        "phase": phase,
        "state": "sent_unconfirmed",
        "remote_request_sent": None,
        "outcome_unknown": True,
        "retry_safe": False,
        **details,
    }


def _mutation_summary(
    mutations: list[dict],
    *,
    failed: bool,
    state_unconfirmed: bool = False,
) -> dict:
    sent_values = [item.get("remote_request_sent") for item in mutations]
    if any(value is True for value in sent_values):
        remote_request_sent = True
    elif any(value is None for value in sent_values):
        remote_request_sent = None
    else:
        remote_request_sent = False
    accepted = [item["action"] for item in mutations if item.get("state") == "accepted"]
    ambiguous = [item["action"] for item in mutations if item.get("state") == "sent_unconfirmed"]
    outcome_unknown = state_unconfirmed or any(item.get("outcome_unknown") is True for item in mutations)
    return {
        "mutations": mutations,
        "accepted_mutations": accepted,
        "ambiguous_mutations": ambiguous,
        "partial_state": failed and bool(accepted or ambiguous),
        "remote_request_sent": remote_request_sent,
        "outcome_unknown": outcome_unknown,
        "retry_safe": all(item.get("retry_safe") is True for item in mutations),
    }


def _boot_failure(
    server_id: str,
    phase: str,
    failure: dict,
    actions: list[str],
    mutations: list[dict],
    *,
    state_unconfirmed: bool = False,
) -> dict:
    result = {"server_id": server_id, **failure, "status": "error", "phase": phase}
    result["actions_completed"] = list(actions)
    result.update(
        _mutation_summary(
            mutations,
            failed=True,
            state_unconfirmed=state_unconfirmed,
        )
    )
    return result


def _validate_image_url(image_url: str) -> str | None:
    if not isinstance(image_url, str) or not image_url.strip():
        return "image_url is required"
    if len(image_url) > 2048 or any(ord(char) < 33 or ord(char) == 127 for char in image_url):
        return "image_url must be a single HTTP(S) URL without whitespace or control characters"
    parsed = urlparse(image_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "image_url must use http or https and include a host"
    if parsed.username is not None or parsed.password is not None:
        return "image_url must not contain embedded credentials"
    return None


def _normalize_server_ids(server_ids: list[str]) -> tuple[list[str] | None, str | None]:
    if not isinstance(server_ids, list) or not server_ids:
        return None, "server_ids must be a non-empty list"
    if len(server_ids) > _MAX_BATCH_SIZE:
        return None, f"At most {_MAX_BATCH_SIZE} server IDs may be requested at once"
    values: list[str] = []
    seen: set[str] = set()
    for value in server_ids:
        if not isinstance(value, str) or not value.strip():
            return None, "Every server_id must be a non-empty string"
        server_id = value.strip()
        if any(not 32 <= ord(char) <= 126 for char in server_id):
            return None, "Invalid server_id"
        if server_id not in seen:
            values.append(server_id)
            seen.add(server_id)
    return values, None


async def _wait_for_media(
    server_id: str,
    image_url: str,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict = {}
    while True:
        last_state = await _get_vm_path_and_state(server_id)
        if last_state.get("inserted") is True and last_state.get("image") == image_url:
            return last_state
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Virtual media did not report the exact requested image as inserted before the verification deadline"
            )
        await asyncio.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


async def _verify_boot_override(server_id: str, mode: str | None) -> dict:
    handler = await _get_handler(server_id)
    response = await _redfish_call(server_id, "GET", handler.SYSTEM_PATH)
    if response.get("status") != "success":
        raise RuntimeError(response.get("message") or "Could not verify boot override")
    data = response.get("data") or {}
    boot = data.get("Boot") or {}
    expected_mode = None
    if mode:
        expected_mode = "UEFI" if mode.strip().lower() == "uefi" else "Legacy"
    verified = (
        boot.get("BootSourceOverrideEnabled") == "Once"
        and boot.get("BootSourceOverrideTarget") == "Cd"
        and (expected_mode is None or boot.get("BootSourceOverrideMode") == expected_mode)
    )
    if not verified:
        raise RuntimeError(
            "BMC did not report BootSourceOverrideEnabled=Once and BootSourceOverrideTarget=Cd; reboot was not sent"
        )
    return {
        "boot_source_override_enabled": boot.get("BootSourceOverrideEnabled"),
        "boot_source_override_target": boot.get("BootSourceOverrideTarget"),
        "boot_source_override_mode": boot.get("BootSourceOverrideMode"),
        "power_state": data.get("PowerState"),
    }


@mcp.tool(description="Declaratively ensure ISO/image is mounted as virtual media (idempotent), in parallel.")
async def inject_media(server_ids: list[str], image_url: str, concurrency: int | None = None) -> list[dict]:
    """Ensure desired ISO is mounted.

    Behavior
    - If same image already mounted: success with message (no change).
    - If different image mounted: eject then insert desired.
    - If none mounted: insert desired.
    """

    normalized_ids, error = _normalize_server_ids(server_ids)
    error = error or _validate_image_url(image_url)
    concurrency = getattr(cfg, "BATCH_CONCURRENCY", 6) if concurrency is None else concurrency
    try:
        concurrency = int(concurrency) if not isinstance(concurrency, bool) else 0
    except (TypeError, ValueError):
        concurrency = 0
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        error = f"concurrency must be between 1 and {_MAX_CONCURRENCY}"
    if error:
        return [{"status": "error", "phase": "validation", "message": error}]

    async def _inject_single(server_id: str) -> dict:
        try:
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            currently_inserted = state["inserted"]
            current_image = state["image"]

            if currently_inserted and current_image == image_url:
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": f"Media {image_url} is already inserted; nothing to do",
                }

            # If something else is inserted, eject it first
            if currently_inserted and current_image != image_url:
                eject_result = await _eject_virtual_media(server_id, vm_path)
                if eject_result.get("status") != "success":
                    return {"server_id": server_id, **eject_result}

            # Insert desired media
            insert_result = await _insert_virtual_media(server_id, vm_path, image_url)
            if insert_result.get("status") == "success":
                return {"server_id": server_id, "status": "success", "message": f"Media {image_url} inserted"}
            return {"server_id": server_id, **insert_result}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    semaphore = asyncio.Semaphore(concurrency)

    async def run(server_id: str) -> dict:
        async with semaphore:
            return await _inject_single(server_id)

    tasks = [run(server_id) for server_id in normalized_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Declaratively ensure no virtual media is mounted (idempotent), in parallel.")
async def eject_media(server_ids: list[str], concurrency: int | None = None) -> list[dict]:
    """Ensure no ISO is mounted.

    Behavior
    - If nothing mounted: success with message (no change).
    - If mounted: eject and report what was ejected.
    """

    normalized_ids, error = _normalize_server_ids(server_ids)
    concurrency = getattr(cfg, "BATCH_CONCURRENCY", 6) if concurrency is None else concurrency
    try:
        concurrency = int(concurrency) if not isinstance(concurrency, bool) else 0
    except (TypeError, ValueError):
        concurrency = 0
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        error = f"concurrency must be between 1 and {_MAX_CONCURRENCY}"
    if error:
        return [{"status": "error", "phase": "validation", "message": error}]

    async def _eject_single(server_id: str) -> dict:
        try:
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            currently_inserted = state["inserted"]
            current_image = state["image"]

            if not currently_inserted:
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": "Nothing ejected because nothing was inserted",
                }

            result = await _eject_virtual_media(server_id, vm_path)
            if result.get("status") == "success":
                return {
                    "server_id": server_id,
                    "status": "success",
                    "message": f"Ejected media {current_image if current_image else ''}".strip(),
                }
            return {"server_id": server_id, **result}
        except Exception as e:
            return {"server_id": server_id, "status": "error", "message": str(e)}

    semaphore = asyncio.Semaphore(concurrency)

    async def run(server_id: str) -> dict:
        async with semaphore:
            return await _eject_single(server_id)

    tasks = [run(server_id) for server_id in normalized_ids]
    return await asyncio.gather(*tasks)


@mcp.tool(description="Ensure ISO is mounted, set one-time boot to CD, and reboot if requested (declarative).")
async def boot_from_iso(
    server_ids: list[str],
    image_url: str,
    mode: str | None = None,
    reboot: bool = True,
    reboot_type: str = "ForceRestart",
    verify: bool = True,
    verification_timeout_seconds: float = 15.0,
    concurrency: int | None = None,
) -> list[dict]:
    """Ensure ISO is mounted and next boot is from CD (Once); reboot by default.

    - If the same ISO is already mounted, it will not re-insert.
    - If a different image is mounted, it will eject and insert the requested ISO.
    - Sets BootSourceOverride to CD Once (and optional mode) before rebooting if requested.
    """

    normalized_ids, ids_error = _normalize_server_ids(server_ids)
    validation_error = ids_error or _validate_image_url(image_url)
    if not isinstance(reboot, bool) or not isinstance(verify, bool):
        validation_error = "reboot and verify must be booleans"
    if mode is not None and str(mode).strip().lower() not in {"uefi", "legacy"}:
        validation_error = "mode must be uefi or legacy when provided"
    allowed_reset_types = {"On", "ForceRestart", "GracefulRestart", "PowerCycle"}
    if reboot_type not in allowed_reset_types:
        validation_error = f"reboot_type must be one of: {', '.join(sorted(allowed_reset_types))}"
    try:
        verification_timeout = float(verification_timeout_seconds)
    except (TypeError, ValueError):
        verification_timeout = 0
    if not 1 <= verification_timeout <= 300:
        validation_error = "verification_timeout_seconds must be between 1 and 300"
    if concurrency is None:
        concurrency = getattr(cfg, "BATCH_CONCURRENCY", 6)
    if isinstance(concurrency, bool):
        concurrency = 0
    try:
        concurrency = int(concurrency)
    except (TypeError, ValueError):
        concurrency = 0
    if not 1 <= concurrency <= _MAX_CONCURRENCY:
        validation_error = f"concurrency must be between 1 and {_MAX_CONCURRENCY}"
    if validation_error:
        return [{"status": "error", "phase": "validation", "message": validation_error}]

    async def _process_single(server_id: str) -> dict:
        actions: list[str] = []
        mutations: list[dict] = []
        phase = "media-state"
        pending_mutation = None
        try:
            verification: dict = {}

            # Ensure media state
            state = await _get_vm_path_and_state(server_id)
            vm_path = state["vm_path"]
            inserted = state["inserted"]
            current_image = state["image"]

            if inserted and current_image == image_url:
                actions.append(f"Media {image_url} already inserted")
            else:
                if inserted and current_image != image_url:
                    phase = "media-eject"
                    pending_mutation = {
                        "action": "eject_media",
                        "previous_image": current_image,
                    }
                    eject_result = await _eject_virtual_media(server_id, vm_path)
                    mutations.append(
                        _mutation_record(
                            "eject_media",
                            phase,
                            eject_result,
                            previous_image=current_image,
                        )
                    )
                    pending_mutation = None
                    if eject_result.get("status") != "success":
                        return _boot_failure(server_id, phase, eject_result, actions, mutations)
                    actions.append(f"Ejected media {current_image}")

                phase = "media-insert"
                pending_mutation = {
                    "action": "insert_media",
                    "requested_image": image_url,
                }
                insert_result = await _insert_virtual_media(server_id, vm_path, image_url)
                mutations.append(
                    _mutation_record(
                        "insert_media",
                        phase,
                        insert_result,
                        requested_image=image_url,
                    )
                )
                pending_mutation = None
                if insert_result.get("status") != "success":
                    return _boot_failure(server_id, phase, insert_result, actions, mutations)
                actions.append(f"Inserted media {image_url}")

            if verify:
                phase = "media-verification"
                media_state = await _wait_for_media(server_id, image_url, verification_timeout)
                verification["media"] = {
                    "inserted": media_state.get("inserted"),
                    "image": media_state.get("image"),
                    "path": media_state.get("vm_path"),
                }
                actions.append("Verified exact media URL is inserted")

            # Ensure boot once to CD
            phase = "boot-override"
            pending_mutation = {
                "action": "set_boot_override",
                "target": "Cd",
                "mode": mode,
            }
            ensure_result = await _ensure_boot_once_single(server_id, "Cd", mode=mode)
            pending_mutation = None
            if ensure_result.get("status") != "success":
                if any(key in ensure_result for key in ("remote_request_sent", "outcome_unknown", "retry_safe")):
                    mutations.append(
                        _mutation_record(
                            "set_boot_override",
                            phase,
                            ensure_result,
                            target="Cd",
                            mode=mode,
                        )
                    )
                return _boot_failure(server_id, phase, ensure_result, actions, mutations)
            boot_override_changed = "already set" not in str(ensure_result.get("message") or "").lower()
            if boot_override_changed:
                mutations.append(
                    _mutation_record(
                        "set_boot_override",
                        phase,
                        {
                            "status": "success",
                            "remote_request_sent": True,
                            "outcome_unknown": False,
                            "retry_safe": False,
                        },
                        target="Cd",
                        mode=mode,
                    )
                )
            actions.append("Boot override set to Cd Once")

            boot_state = None
            if verify:
                phase = "boot-verification"
                boot_state = await _verify_boot_override(server_id, mode)
                verification["boot"] = boot_state
                actions.append("Verified Cd Once boot override")

            # Reboot if requested
            reset_request = None
            if reboot:
                phase = "reset-request"
                handler = await _get_handler(server_id)
                reset_path = f"{handler.SYSTEM_PATH}/Actions/ComputerSystem.Reset"
                observed_power_state = boot_state.get("power_state") if boot_state else ensure_result.get("power_state")
                actual_reset_type = "On" if observed_power_state == "Off" else reboot_type
                pending_mutation = {
                    "action": "request_reset",
                    "reset_type": actual_reset_type,
                }
                reset_result = await _redfish_call(server_id, "POST", reset_path, {"ResetType": actual_reset_type})
                mutations.append(
                    _mutation_record(
                        "request_reset",
                        phase,
                        reset_result,
                        reset_type=actual_reset_type,
                    )
                )
                pending_mutation = None
                if reset_result.get("status") != "success":
                    return _boot_failure(server_id, phase, reset_result, actions, mutations)
                actions.append(f"Reset request accepted: {actual_reset_type}")
                reset_request = {
                    "requested_type": actual_reset_type,
                    "accepted": True,
                    "power_state_verified": False,
                }

            result = {
                "server_id": server_id,
                "status": "success",
                "phase": "complete",
                "message": "; ".join(actions) if actions else "No changes needed",
                "verification": verification,
                "actions_completed": list(actions),
            }
            if reset_request is not None:
                result["reset_request"] = reset_request
            result.update(_mutation_summary(mutations, failed=False))
            return result
        except Exception as e:
            if pending_mutation is not None:
                details = dict(pending_mutation)
                action = details.pop("action")
                mutations.append(_ambiguous_mutation(action, phase, **details))
            state_unconfirmed = phase in {"media-verification", "boot-verification"} and bool(mutations)
            return _boot_failure(
                server_id,
                phase,
                {"message": str(e)},
                actions,
                mutations,
                state_unconfirmed=state_unconfirmed,
            )

    semaphore = asyncio.Semaphore(concurrency)

    async def run(server_id: str) -> dict:
        async with semaphore:
            return await _process_single(server_id)

    tasks = [run(sid) for sid in normalized_ids]
    return await asyncio.gather(*tasks)
