#!/usr/bin/env python3
"""Fetch recent GroupMe messages for selected groups and write digest artifacts.

Configuration:
  GROUPME_ACCESS_TOKEN   required; store as a GitHub Actions secret
  GROUPME_GROUP_NAMES    optional comma-separated exact group names
  GROUPME_GROUP_IDS      optional comma-separated GroupMe group IDs
  GROUPME_LOOKBACK_HOURS optional; defaults to 36

At least one of GROUPME_GROUP_NAMES or GROUPME_GROUP_IDS is required.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

API_BASE = "https://api.groupme.com/v3"
LOCAL_TZ = ZoneInfo("America/Chicago")


def csv_env(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


class GroupMe:
    def __init__(self, token: str) -> None:
        self.token = token

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "family-board-groupme-digest/1.0",
                "X-Access-Token": self.token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            # GroupMe returns 304 when a message page has no results.
            if exc.code == 304:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GroupMe API returned HTTP {exc.code}: {detail}") from exc
        meta = payload.get("meta", {})
        if meta.get("code") not in (200, 201):
            raise RuntimeError(f"GroupMe API error: {meta}")
        return payload.get("response")

    def groups(self) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.get("/groups", {"page": page, "per_page": 100, "omit": "memberships"}) or []
            groups.extend(batch)
            if len(batch) < 100:
                return groups
            page += 1

    def messages_since(self, group_id: str, cutoff_epoch: int) -> list[dict[str, Any]]:
        messages: dict[str, dict[str, Any]] = {}
        before_id: str | None = None

        while True:
            params: dict[str, Any] = {"limit": 100}
            if before_id:
                params["before_id"] = before_id

            response = self.get(f"/groups/{group_id}/messages", params)
            if not response:
                break

            batch = response.get("messages", [])
            if not batch:
                break

            for message in batch:
                created_at = int(message.get("created_at") or 0)
                if created_at >= cutoff_epoch:
                    messages[str(message["id"])] = message

            oldest = min(batch, key=lambda m: int(m.get("created_at") or 0))
            oldest_epoch = int(oldest.get("created_at") or 0)
            if oldest_epoch < cutoff_epoch or len(batch) < 100:
                break
            before_id = str(oldest["id"])

        return sorted(messages.values(), key=lambda m: int(m.get("created_at") or 0))


def resolve_groups(
    available: list[dict[str, Any]],
    wanted_names: list[str],
    wanted_ids: list[str],
) -> list[dict[str, Any]]:
    by_id = {str(group["id"]): group for group in available}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for group in available:
        by_name.setdefault(str(group.get("name", "")).casefold(), []).append(group)

    selected: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for group_id in wanted_ids:
        group = by_id.get(group_id)
        if not group:
            errors.append(f"Unknown GroupMe group ID: {group_id}")
        else:
            selected[group_id] = group

    for name in wanted_names:
        matches = by_name.get(name.casefold(), [])
        if len(matches) == 1:
            selected[str(matches[0]["id"])] = matches[0]
        elif not matches:
            errors.append(f'No GroupMe group named exactly "{name}"')
        else:
            ids = ", ".join(str(group["id"]) for group in matches)
            errors.append(f'Ambiguous GroupMe group name "{name}" (IDs: {ids})')

    if errors:
        visible = ", ".join(sorted(str(group.get("name", "")) for group in available))
        raise RuntimeError("\n".join(errors) + f"\nAvailable active groups: {visible}")

    return list(selected.values())


def clean_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for attachment in attachments or []:
        item = {"type": attachment.get("type", "unknown")}
        for key in ("url", "name", "lat", "lng"):
            if attachment.get(key) is not None:
                item[key] = attachment[key]
        cleaned.append(item)
    return cleaned


def normalize_message(group: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    epoch = int(message.get("created_at") or 0)
    utc_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    local_dt = utc_dt.astimezone(LOCAL_TZ)
    return {
        "id": str(message.get("id", "")),
        "group_id": str(group["id"]),
        "group_name": group.get("name", ""),
        "created_at": utc_dt.isoformat(),
        "created_at_central": local_dt.isoformat(),
        "sender": message.get("name") or "",
        "user_id": str(message.get("user_id") or ""),
        "text": message.get("text") or "",
        "attachments": clean_attachments(message.get("attachments")),
    }


def write_markdown(path: Path, generated_at: datetime, cutoff: datetime, messages: list[dict[str, Any]]) -> None:
    lines = [
        "# GroupMe school digest source",
        "",
        f"Generated: {generated_at.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %I:%M %p %Z')}",
        f"Window starts: {cutoff.astimezone(LOCAL_TZ).strftime('%Y-%m-%d %I:%M %p %Z')}",
        "",
    ]
    if not messages:
        lines.append("No messages found in the configured groups during this window.")
    else:
        current_group = None
        for message in messages:
            if message["group_name"] != current_group:
                current_group = message["group_name"]
                lines.extend([f"## {current_group}", ""])
            local = datetime.fromisoformat(message["created_at_central"])
            text = message["text"].strip() or "(no text)"
            lines.append(
                f"- **{local.strftime('%a %b %-d, %-I:%M %p')} — {message['sender']}**: {text}"
            )
            if message["attachments"]:
                types = ", ".join(str(item["type"]) for item in message["attachments"])
                lines.append(f"  - Attachments: {types}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="artifacts/groupme")
    parser.add_argument("--list-groups", action="store_true")
    args = parser.parse_args()

    token = os.getenv("GROUPME_ACCESS_TOKEN")
    if not token:
        print("GROUPME_ACCESS_TOKEN is required", file=sys.stderr)
        return 2

    client = GroupMe(token)
    groups = client.groups()

    if args.list_groups:
        for group in sorted(groups, key=lambda g: str(g.get("name", "")).casefold()):
            print(f'{group["id"]}\t{group.get("name", "")}')
        return 0

    wanted_names = csv_env("GROUPME_GROUP_NAMES")
    wanted_ids = csv_env("GROUPME_GROUP_IDS")
    if not wanted_names and not wanted_ids:
        print("Set GROUPME_GROUP_NAMES and/or GROUPME_GROUP_IDS", file=sys.stderr)
        return 2

    try:
        lookback_hours = int(os.getenv("GROUPME_LOOKBACK_HOURS", "36"))
        if lookback_hours < 1 or lookback_hours > 168:
            raise ValueError
    except ValueError:
        print("GROUPME_LOOKBACK_HOURS must be an integer from 1 to 168", file=sys.stderr)
        return 2

    selected = resolve_groups(groups, wanted_names, wanted_ids)
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=lookback_hours)
    cutoff_epoch = int(cutoff.timestamp())

    normalized: list[dict[str, Any]] = []
    for group in selected:
        for message in client.messages_since(str(group["id"]), cutoff_epoch):
            normalized.append(normalize_message(group, message))
    normalized.sort(key=lambda m: m["created_at"])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": generated_at.isoformat(),
        "window_start": cutoff.isoformat(),
        "lookback_hours": lookback_hours,
        "groups": [{"id": str(g["id"]), "name": g.get("name", "")} for g in selected],
        "message_count": len(normalized),
        "messages": normalized,
    }
    (output_dir / "groupme_messages.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_markdown(output_dir / "groupme_messages.md", generated_at, cutoff, normalized)
    print(f"Wrote {len(normalized)} messages from {len(selected)} groups to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
