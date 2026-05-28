"""
Background task: periodically checks signing status for all subscribed devices
and notifies users about changes.
"""
import logging
from datetime import datetime

import database as db
import ipsw_api as api

logger = logging.getLogger(__name__)


def _make_key(fw: dict) -> str:
    return f"{fw['version']}|{fw['buildid']}"


def _diff(old: list, new: list) -> tuple[list, list, list]:
    """
    Compare old and new firmware lists.
    Returns (newly_signed, newly_unsigned, unchanged_signed).
    Each item is a fw dict with 'version', 'buildid', 'signed'.
    """
    old_map = {_make_key(f): f for f in old}
    new_map = {_make_key(f): f for f in new}

    newly_signed   = []
    newly_unsigned = []

    # Items that changed from unsigned→signed
    for key, fw in new_map.items():
        if fw["signed"]:
            if key not in old_map or not old_map[key]["signed"]:
                newly_signed.append(fw)

    # Items that changed from signed→unsigned
    for key, fw in old_map.items():
        if fw["signed"]:
            if key not in new_map or not new_map[key]["signed"]:
                newly_unsigned.append(fw)

    unchanged_signed = [f for f in new_map.values() if f["signed"] and _make_key(f) not in
                        {_make_key(x) for x in newly_signed}]

    return newly_signed, newly_unsigned, unchanged_signed


def _source_emoji(source: str) -> str:
    return "🟢" if source == "release" else "🔵"


def _build_notification(
    device_name: str,
    identifier: str,
    source: str,
    newly_signed: list,
    newly_unsigned: list,
) -> str:
    label = "Release" if source == "release" else "Beta/Dev"
    emoji = _source_emoji(source)
    lines = [
        f"<b>{emoji} Signing change — {device_name}</b> <code>({identifier})</code>",
        f"<i>Source: {label}</i>",
        "",
    ]

    if newly_signed:
        lines.append("✅ <b>Now signed:</b>")
        for fw in newly_signed:
            lines.append(f"  • {fw['version']} ({fw['buildid']})")

    if newly_unsigned:
        if newly_signed:
            lines.append("")
        lines.append("❌ <b>No longer signed:</b>")
        for fw in newly_unsigned:
            lines.append(f"  • {fw['version']} ({fw['buildid']})")

    lines.append(f"\n🕐 {datetime.now().strftime('%d.%m.%Y %H:%M')} UTC")
    return "\n".join(lines)


async def check_device(app, identifier: str, device_name: str):
    """Check one device and send notifications if signing status changed."""
    for source in ("release", "dev"):
        if source == "release":
            _, all_fw, _ = await api.get_release_firmwares(identifier)
        else:
            _, all_fw = await api.get_dev_firmwares(identifier)

        if not all_fw:
            logger.warning(
                "Empty firmware list for %s (%s) — upstream HTML/API may have changed",
                identifier, source,
            )
            continue

        old_snapshot = db.get_snapshot(identifier, source)

        if old_snapshot is None:
            # First run — just save, no notification
            db.save_snapshot(identifier, source, all_fw)
            continue

        newly_signed, newly_unsigned, _ = _diff(old_snapshot, all_fw)

        if newly_signed or newly_unsigned:
            db.save_snapshot(identifier, source, all_fw)
            text = _build_notification(
                device_name, identifier, source, newly_signed, newly_unsigned
            )
            subscribers = db.get_subscriptions_for_device(identifier)
            for sub in subscribers:
                try:
                    await app.bot.send_message(
                        chat_id=sub["chat_id"],
                        text=text,
                        parse_mode="HTML",
                    )
                    logger.info(
                        "Notified user %s about %s (%s)", sub["user_id"], identifier, source
                    )
                except Exception as e:
                    logger.error(
                        "Failed to notify user %s: %s", sub["user_id"], e
                    )


async def check_all(context):
    """Job callback: check every subscribed device."""
    app = context.application
    subscriptions = db.get_all_subscriptions()

    # Deduplicate by device
    seen = {}
    for sub in subscriptions:
        ident = sub["device_identifier"]
        if ident not in seen:
            seen[ident] = sub["device_name"]

    logger.info("Checking %d unique devices", len(seen))
    for identifier, device_name in seen.items():
        try:
            await check_device(app, identifier, device_name)
        except Exception as e:
            logger.error("Error checking %s: %s", identifier, e)
