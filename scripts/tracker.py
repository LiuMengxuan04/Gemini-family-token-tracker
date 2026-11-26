#!/usr/bin/env python3
"""Family Token Tracker for Antigravity.

Tracks token consumption from Antigravity transcript.jsonl files into a local
SQLite database, aggregates total usage per member+device within the active
Cycle (starting from `since`, defaulting to plugin installation time), supports
one-person reset with whole-family auto-follow via Notion, and runs zero-LLM
background auto-sync on every Stop hook.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import getpass
import json
import os
import pathlib
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
DATA_DIR = pathlib.Path(
    os.environ.get("FAMILY_TOKEN_TRACKER_DATA")
    or (pathlib.Path.home() / ".gemini" / "antigravity" / "plugin_data" / "family-token-tracker")
)
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "usage.db"
SYNC_LOCK_PATH = DATA_DIR / "sync.lock"
LAST_SYNC_PATH = DATA_DIR / "last_sync.json"

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_cycle_name(existing_cycle: str = "") -> str:
    now_local = dt.datetime.now().astimezone()
    day_name = now_local.strftime("%Y-%m-%d")
    if existing_cycle != day_name:
        return day_name
    return now_local.strftime("%Y-%m-%d %H:%M")


def default_config() -> Dict[str, Any]:
    return {
        "member": getpass.getuser(),
        "device": socket.gethostname().split(".")[0],
        "cycle": default_cycle_name(),
        "since": utc_now_iso(),
        "daily_limit": 0,
        "monthly_limit": 0,
        "count_cache_read": False,
        "warn_threshold_pct": 80,
        "remind_cooldown_minutes": 60,
        "auto_sync": True,
        "auto_follow_reset": True,
        "notion": {
            "database_url": "",
            "database_id": "",
            "data_source_id": "",
            "api_token": "",
        },
    }


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def extract_notion_id(url_or_id: str) -> str:
    """Extract a 32-hex Notion ID from a Notion URL or raw UUID."""
    if not url_or_id:
        return ""
    cleaned = url_or_id.strip()
    m_uuid = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        cleaned,
    )
    if m_uuid:
        return m_uuid.group(1).replace("-", "").lower()
    path_only = cleaned.split("?")[0].split("#")[0]
    matches = re.findall(r"([0-9a-fA-F]{32})", path_only)
    if matches:
        return matches[-1].lower()
    return ""


def normalize_since(value: str) -> str:
    """Normalize user-supplied --since value: 'now', 'all'/empty, YYYY-MM-DD, or ISO timestamp."""
    v = value.strip()
    if not v or v.lower() in ("all", "none", "clear"):
        return ""
    if v.lower() == "now":
        return utc_now_iso()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    try:
        parsed = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return v


def since_to_epoch(since: str) -> float:
    """Convert a `since` string (YYYY-MM-DD or ISO timestamp) to UTC epoch seconds for comparison."""
    if not since:
        return 0.0
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
            local_dt = dt.datetime.strptime(since, "%Y-%m-%d").astimezone()
            return local_dt.timestamp()
        parsed = dt.datetime.fromisoformat(since.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def load_config() -> Dict[str, Any]:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return default_config()
    merged = default_config()
    has_since = "since" in raw
    has_cycle = "cycle" in raw and bool(str(raw.get("cycle") or "").strip())
    for k, v in raw.items():
        if k == "notion" and isinstance(v, dict):
            merged["notion"].update(v)
        else:
            merged[k] = v
    if not has_cycle:
        since_val = str(merged.get("since") or "")
        merged["cycle"] = since_val[:10] if len(since_val) >= 10 else default_cycle_name()
    if not has_since or not has_cycle:
        save_config(merged)
    if not merged["notion"].get("database_id") and merged["notion"].get("database_url"):
        merged["notion"]["database_id"] = extract_notion_id(merged["notion"]["database_url"])
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_data_dir()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_PATH)


def save_last_sync(status: Dict[str, Any]) -> None:
    ensure_data_dir()
    tmp = LAST_SYNC_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    tmp.replace(LAST_SYNC_PATH)


def open_db() -> sqlite3.Connection:
    ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS token_events (
            conversation_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            local_date TEXT NOT NULL,
            local_month TEXT NOT NULL,
            member TEXT NOT NULL,
            device TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (conversation_id, step_index)
        );
        CREATE INDEX IF NOT EXISTS idx_token_events_date ON token_events(local_date);
        CREATE INDEX IF NOT EXISTS idx_token_events_month ON token_events(local_month);
        CREATE INDEX IF NOT EXISTS idx_token_events_created ON token_events(created_at);

        CREATE TABLE IF NOT EXISTS notion_sync_log (
            sync_key TEXT PRIMARY KEY,
            notion_page_id TEXT NOT NULL,
            last_synced_input INTEGER NOT NULL DEFAULT 0,
            last_synced_cache_read INTEGER NOT NULL DEFAULT 0,
            last_synced_output INTEGER NOT NULL DEFAULT 0,
            last_synced_calls INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_state (
            key TEXT PRIMARY KEY,
            last_reminded_at TEXT NOT NULL
        );
        """
    )
    return conn


def parse_utc_to_local_date(ts: str) -> Tuple[str, str]:
    now_local = dt.datetime.now().astimezone()
    if not ts:
        return now_local.strftime("%Y-%m-%d"), now_local.strftime("%Y-%m")
    try:
        normalized = ts.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        local_dt = parsed.astimezone()
        return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%Y-%m")
    except Exception:
        return now_local.strftime("%Y-%m-%d"), now_local.strftime("%Y-%m")


def since_filter_sql(since: str) -> Tuple[str, Tuple[Any, ...]]:
    if not since:
        return "1 = 1", ()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        return "local_date >= ?", (since,)
    return "created_at >= ?", (since,)


def ingest_transcript(
    conn: sqlite3.Connection,
    transcript_path: str,
    conversation_id: str,
    cfg: Dict[str, Any],
) -> int:
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0
    member = str(cfg.get("member") or getpass.getuser())
    device = str(cfg.get("device") or socket.gethostname().split(".")[0])
    inserted = 0
    rows: List[Tuple[Any, ...]] = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or '"PLANNER_RESPONSE"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("type") != "PLANNER_RESPONSE":
                    continue
                step_idx = entry.get("step_index")
                if step_idx is None:
                    continue
                in_tok = int(entry.get("input_tokens") or 0)
                cache_tok = int(entry.get("cache_read_tokens") or 0)
                out_tok = int(entry.get("output_tokens") or 0)
                if in_tok == 0 and cache_tok == 0 and out_tok == 0:
                    continue
                created_at = str(entry.get("created_at") or utc_now_iso())
                local_date, local_month = parse_utc_to_local_date(created_at)
                model = str(entry.get("model") or "unknown")
                rows.append(
                    (
                        conversation_id,
                        int(step_idx),
                        created_at,
                        local_date,
                        local_month,
                        member,
                        device,
                        model,
                        in_tok,
                        cache_tok,
                        out_tok,
                    )
                )
    except Exception:
        return 0

    if rows:
        cur = conn.cursor()
        before = conn.total_changes
        cur.executemany(
            """
            INSERT OR IGNORE INTO token_events (
                conversation_id, step_index, created_at, local_date, local_month,
                member, device, model, input_tokens, cache_read_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        inserted = conn.total_changes - before
    return inserted


def query_period_totals(
    conn: sqlite3.Connection,
    where_sql: str,
    params: Tuple[Any, ...],
) -> Dict[str, int]:
    row = conn.execute(
        f"""
        SELECT
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COUNT(*) AS calls
        FROM token_events
        WHERE {where_sql}
        """,
        params,
    ).fetchone()
    in_tok = int(row["input_tokens"])
    cache_tok = int(row["cache_read_tokens"])
    out_tok = int(row["output_tokens"])
    return {
        "input_tokens": in_tok,
        "cache_read_tokens": cache_tok,
        "output_tokens": out_tok,
        "billable_tokens": in_tok + out_tok,
        "total_tokens": in_tok + cache_tok + out_tok,
        "calls": int(row["calls"]),
    }


def counted_tokens(totals: Dict[str, int], cfg: Dict[str, Any]) -> int:
    if cfg.get("count_cache_read"):
        return totals["total_tokens"]
    return totals["billable_tokens"]


def fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def build_progress_bar(used: int, limit: int, calls: int) -> str:
    width = 10
    if limit > 0:
        pct = (used / limit) * 100.0
        filled = min(width, max(0, int(round((used / limit) * width))))
        if pct >= 100.0:
            bar = "🟥" * width
        elif pct >= 80.0:
            bar = "🟧" * filled + "⬜" * (width - filled)
        else:
            bar = "🟩" * filled + "⬜" * (width - filled)
        return f"{bar} {pct:.1f}% ({fmt_num(used)} / {fmt_num(limit)})"

    if used <= 0:
        filled = 0
    elif used < 100_000:
        filled = 1
    elif used < 500_000:
        filled = 2
    elif used < 1_000_000:
        filled = 3
    elif used < 2_500_000:
        filled = 4
    elif used < 5_000_000:
        filled = 5
    elif used < 10_000_000:
        filled = 6
    elif used < 25_000_000:
        filled = 7
    elif used < 50_000_000:
        filled = 8
    elif used < 100_000_000:
        filled = 9
    else:
        filled = 10
    bar = "🟦" * filled + "⬜" * (width - filled)
    return f"{bar} {fmt_num(used)} ({calls} calls)"


def should_remind(conn: sqlite3.Connection, key: str, cooldown_minutes: int) -> bool:
    if cooldown_minutes <= 0:
        return True
    row = conn.execute(
        "SELECT last_reminded_at FROM alert_state WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return True
    try:
        last_dt = dt.datetime.fromisoformat(row["last_reminded_at"])
        now_dt = dt.datetime.now(dt.timezone.utc)
        elapsed = (now_dt - last_dt).total_seconds() / 60.0
        return elapsed >= cooldown_minutes
    except Exception:
        return True


def mark_reminded(conn: sqlite3.Connection, key: str) -> None:
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO alert_state (key, last_reminded_at) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET last_reminded_at = excluded.last_reminded_at
        """,
        (key, now_iso),
    )
    conn.commit()


def spawn_background_sync(cfg: Dict[str, Any]) -> None:
    if not cfg.get("auto_sync", True):
        return
    notion = cfg.get("notion") or {}
    token = str(notion.get("api_token") or os.environ.get("NOTION_API_TOKEN") or "").strip()
    db_id = str(notion.get("database_id") or extract_notion_id(notion.get("database_url", ""))).strip()
    if not token or not db_id:
        return
    try:
        subprocess.Popen(
            [sys.executable, str(pathlib.Path(__file__).resolve()), "sync-notion-direct", "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def cmd_hook_stop(_args: argparse.Namespace) -> None:
    try:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else {}
        conv_id = str(payload.get("conversationId") or "unknown")
        transcript_path = str(payload.get("transcriptPath") or "")
        cfg = load_config()
        conn = open_db()
        try:
            ingest_transcript(conn, transcript_path, conv_id, cfg)
        finally:
            conn.close()
        spawn_background_sync(cfg)
    except Exception:
        pass
    print("{}")


def cmd_hook_pre_invocation(_args: argparse.Namespace) -> None:
    output: Dict[str, Any] = {}
    try:
        raw = sys.stdin.read().strip()
        payload = json.loads(raw) if raw else {}
        conv_id = str(payload.get("conversationId") or "unknown")
        transcript_path = str(payload.get("transcriptPath") or "")
        cfg = load_config()
        conn = open_db()
        try:
            if transcript_path:
                ingest_transcript(conn, transcript_path, conv_id, cfg)

            daily_limit = int(cfg.get("daily_limit") or 0)
            monthly_limit = int(cfg.get("monthly_limit") or 0)
            if daily_limit <= 0 and monthly_limit <= 0:
                print("{}")
                return

            now_local = dt.datetime.now().astimezone()
            today_str = now_local.strftime("%Y-%m-%d")
            month_str = now_local.strftime("%Y-%m")
            s_sql, s_params = since_filter_sql(str(cfg.get("since") or ""))

            daily_totals = query_period_totals(
                conn, f"local_date = ? AND ({s_sql})", (today_str, *s_params)
            )
            monthly_totals = query_period_totals(
                conn, f"local_month = ? AND ({s_sql})", (month_str, *s_params)
            )

            daily_used = counted_tokens(daily_totals, cfg)
            monthly_used = counted_tokens(monthly_totals, cfg)
            warn_pct = float(cfg.get("warn_threshold_pct") or 80)

            alerts: List[str] = []
            exceeded = False

            if daily_limit > 0:
                pct = (daily_used / daily_limit) * 100.0
                if pct >= 100.0:
                    exceeded = True
                    alerts.append(
                        f"Today / 今日: **{fmt_num(daily_used)}** / {fmt_num(daily_limit)} ({pct:.0f}%, exceeded / 已超额)"
                    )
                elif pct >= warn_pct:
                    alerts.append(
                        f"Today / 今日: **{fmt_num(daily_used)}** / {fmt_num(daily_limit)} ({pct:.0f}%)"
                    )

            if monthly_limit > 0:
                pct = (monthly_used / monthly_limit) * 100.0
                if pct >= 100.0:
                    exceeded = True
                    alerts.append(
                        f"This Month / 本月: **{fmt_num(monthly_used)}** / {fmt_num(monthly_limit)} ({pct:.0f}%, exceeded / 已超额)"
                    )
                elif pct >= warn_pct:
                    alerts.append(
                        f"This Month / 本月: **{fmt_num(monthly_used)}** / {fmt_num(monthly_limit)} ({pct:.0f}%)"
                    )

            if alerts:
                cooldown = int(cfg.get("remind_cooldown_minutes") or 60)
                alert_key = f"conv:{conv_id}"
                if should_remind(conn, alert_key, cooldown):
                    mark_reminded(conn, alert_key)
                    icon = "🚨" if exceeded else "⚠️"
                    member = cfg.get("member") or getpass.getuser()
                    summary = " | ".join(alerts)
                    markdown = (
                        f"> {icon} **Family Token Quota Notice / 家庭 Token 额度提醒 ({member})**: {summary}. "
                        "Gentle reminder only — your chat continues normally. / 仅温和提醒，不打断当前对话。"
                    )
                    output = {
                        "injectSteps": [
                            {
                                "systemMessage": {
                                    "systemMessage": "",
                                    "metadata": {"user_markdown": markdown},
                                }
                            }
                        ]
                    }
        finally:
            conn.close()
    except Exception:
        output = {}
    print(json.dumps(output, ensure_ascii=False))


def get_summary_pending_item(
    conn: sqlite3.Connection, cfg: Dict[str, Any], force: bool = False
) -> List[Dict[str, Any]]:
    """Build the single aggregated summary row for this cycle + member + device."""
    member = str(cfg.get("member") or getpass.getuser())
    device = str(cfg.get("device") or socket.gethostname().split(".")[0])
    cycle = str(cfg.get("cycle") or default_cycle_name())
    since = str(cfg.get("since") or "")
    s_sql, s_params = since_filter_sql(since)

    totals = query_period_totals(conn, s_sql, s_params)
    sync_key = f"summary|{cycle}|{member}|{device}"

    prev_row = conn.execute(
        "SELECT * FROM notion_sync_log WHERE sync_key = ?", (sync_key,)
    ).fetchone()
    prev = dict(prev_row) if prev_row else None

    if prev and not force:
        if (
            prev["last_synced_input"] == totals["input_tokens"]
            and prev["last_synced_cache_read"] == totals["cache_read_tokens"]
            and prev["last_synced_output"] == totals["output_tokens"]
            and prev["last_synced_calls"] == totals["calls"]
        ):
            return []

    used = counted_tokens(totals, cfg)
    limit = int(cfg.get("monthly_limit") or cfg.get("daily_limit") or 0)
    progress_text = build_progress_bar(used, limit, totals["calls"])
    now_iso = utc_now_iso()
    since_date_val = since if since else dt.datetime.now().astimezone().strftime("%Y-%m-%d")

    return [
        {
            "action": "update" if prev else "create",
            "sync_key": sync_key,
            "notion_page_id": prev["notion_page_id"] if prev else None,
            "cycle": cycle,
            "member": member,
            "device": device,
            "since": since_date_val,
            "progress": progress_text,
            "last_updated": now_iso,
            "properties": {
                "Record": member,
                "Cycle": cycle,
                "Member": member,
                "Device": device,
                "Progress": progress_text,
                "date:Since:start": since_date_val,
                "date:Since:is_datetime": 1 if "T" in since_date_val else 0,
                "date:Last Updated:start": now_iso,
                "date:Last Updated:is_datetime": 1,
                "Input Tokens": totals["input_tokens"],
                "Cache Read Tokens": totals["cache_read_tokens"],
                "Output Tokens": totals["output_tokens"],
                "Billable Tokens": totals["billable_tokens"],
                "Total Tokens": totals["total_tokens"],
                "Calls": totals["calls"],
                "Sync Key": sync_key,
            },
            "raw": totals,
        }
    ]


def notion_api_request(
    token: str, method: str, path: str, body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    url = f"{NOTION_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_summary_schema(token: str, db_id: str) -> None:
    """Ensure the target Notion database has Cycle, Progress, Since, and Last Updated."""
    try:
        notion_api_request(
            token,
            "PATCH",
            f"/databases/{db_id}",
            {
                "properties": {
                    "Cycle": {"select": {}},
                    "Progress": {"rich_text": {}},
                    "Since": {"date": {}},
                    "Last Updated": {"date": {}},
                }
            },
        )
    except Exception:
        pass


def check_remote_latest_cycle(
    token: str, db_id: str, cfg: Dict[str, Any]
) -> Optional[Dict[str, str]]:
    """Check if another family member started a newer Cycle in Notion.

    If `remote_since` is strictly newer than local `since` and `remote_cycle`
    differs from local `cycle`, automatically adopt the new cycle & since!
    """
    if not cfg.get("auto_follow_reset", True):
        return None
    try:
        res = notion_api_request(
            token,
            "POST",
            f"/databases/{db_id}/query",
            {
                "filter": {"property": "Cycle", "select": {"is_not_empty": True}},
                "sorts": [{"property": "Since", "direction": "descending"}],
                "page_size": 1,
            },
        )
        results = res.get("results") or []
        if not results:
            return None
        props = results[0].get("properties") or {}
        cycle_obj = (props.get("Cycle") or {}).get("select") or {}
        remote_cycle = str(cycle_obj.get("name") or "").strip()
        since_obj = (props.get("Since") or {}).get("date") or {}
        remote_since = normalize_since(str(since_obj.get("start") or ""))
        if not remote_cycle or not remote_since:
            return None

        local_cycle = str(cfg.get("cycle") or "")
        local_since = str(cfg.get("since") or "")
        if remote_cycle != local_cycle and since_to_epoch(remote_since) > since_to_epoch(local_since) + 1.0:
            cfg["cycle"] = remote_cycle
            cfg["since"] = remote_since
            save_config(cfg)
            return {"cycle": remote_cycle, "since": remote_since}
    except Exception:
        return None
    return None


def build_notion_rest_properties(item: Dict[str, Any]) -> Dict[str, Any]:
    raw = item["raw"]
    return {
        "Record": {"title": [{"text": {"content": item["member"]}}]},
        "Cycle": {"select": {"name": item["cycle"]}},
        "Member": {"select": {"name": item["member"]}},
        "Device": {"rich_text": [{"text": {"content": item["device"]}}]},
        "Progress": {"rich_text": [{"text": {"content": item["progress"]}}]},
        "Since": {"date": {"start": item["since"]}},
        "Last Updated": {"date": {"start": item["last_updated"]}},
        "Input Tokens": {"number": raw["input_tokens"]},
        "Cache Read Tokens": {"number": raw["cache_read_tokens"]},
        "Output Tokens": {"number": raw["output_tokens"]},
        "Billable Tokens": {"number": raw["billable_tokens"]},
        "Total Tokens": {"number": raw["total_tokens"]},
        "Calls": {"number": raw["calls"]},
        "Sync Key": {"rich_text": [{"text": {"content": item["sync_key"]}}]},
    }


def upsert_sync_log(conn: sqlite3.Connection, item: Dict[str, Any], page_id: str) -> None:
    raw = item["raw"]
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO notion_sync_log (
            sync_key, notion_page_id,
            last_synced_input, last_synced_cache_read,
            last_synced_output, last_synced_calls, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sync_key) DO UPDATE SET
            notion_page_id = excluded.notion_page_id,
            last_synced_input = excluded.last_synced_input,
            last_synced_cache_read = excluded.last_synced_cache_read,
            last_synced_output = excluded.last_synced_output,
            last_synced_calls = excluded.last_synced_calls,
            updated_at = excluded.updated_at
        """,
        (
            item["sync_key"],
            page_id,
            int(raw["input_tokens"]),
            int(raw["cache_read_tokens"]),
            int(raw["output_tokens"]),
            int(raw["calls"]),
            now_iso,
        ),
    )
    conn.commit()


def run_direct_sync(
    cfg: Dict[str, Any],
    force: bool = False,
    quiet: bool = False,
    cleanup_legacy: bool = False,
    skip_remote_check: bool = False,
) -> Dict[str, Any]:
    ensure_data_dir()
    notion = cfg.get("notion") or {}
    token = str(notion.get("api_token") or os.environ.get("NOTION_API_TOKEN") or "").strip()
    db_id = str(notion.get("database_id") or extract_notion_id(notion.get("database_url", ""))).strip()

    if not token or not db_id:
        res = {
            "ok": False,
            "error": "Missing notion.api_token or notion.database_id (or database_url).",
        }
        if not quiet:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return res

    with SYNC_LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            res = {"ok": True, "skipped": "another sync is in progress"}
            if not quiet:
                print(json.dumps(res))
            return res

        conn = open_db()
        created = 0
        updated = 0
        archived_legacy = 0
        followed_cycle = None
        errors: List[str] = []
        try:
            if cleanup_legacy:
                ensure_summary_schema(token, db_id)
                q_all = notion_api_request(token, "POST", f"/databases/{db_id}/query", {"page_size": 100})
                for p in q_all.get("results") or []:
                    sk_prop = (p.get("properties") or {}).get("Sync Key", {}).get("rich_text") or []
                    sk_val = "".join(t.get("plain_text", "") for t in sk_prop)
                    # Keep only new 4-part summary|<cycle>|<member>|<device> keys
                    if len(sk_val.split("|")) != 4 or not sk_val.startswith("summary|"):
                        notion_api_request(token, "PATCH", f"/pages/{p['id']}", {"archived": True})
                        archived_legacy += 1
                conn.execute("DELETE FROM notion_sync_log")
                conn.commit()

            if not skip_remote_check:
                followed_cycle = check_remote_latest_cycle(token, db_id, cfg)

            pending = get_summary_pending_item(
                conn, cfg, force=force or cleanup_legacy or bool(followed_cycle)
            )
            for item in pending:
                try:
                    props = build_notion_rest_properties(item)
                    page_id = item.get("notion_page_id")

                    if not page_id:
                        ensure_summary_schema(token, db_id)
                        q_res = notion_api_request(
                            token,
                            "POST",
                            f"/databases/{db_id}/query",
                            {
                                "filter": {
                                    "property": "Sync Key",
                                    "rich_text": {"equals": item["sync_key"]},
                                },
                                "page_size": 1,
                            },
                        )
                        results = q_res.get("results") or []
                        if results:
                            page_id = results[0].get("id")

                    if page_id:
                        notion_api_request(
                            token,
                            "PATCH",
                            f"/pages/{page_id}",
                            {"properties": props},
                        )
                        updated += 1
                    else:
                        c_res = notion_api_request(
                            token,
                            "POST",
                            "/pages",
                            {
                                "parent": {"database_id": db_id},
                                "properties": props,
                            },
                        )
                        page_id = str(c_res.get("id") or "")
                        created += 1

                    if page_id:
                        upsert_sync_log(conn, item, page_id)
                except urllib.error.HTTPError as e:
                    err_body = e.read().decode("utf-8", errors="replace")[:300]
                    errors.append(f"{item['sync_key']}: HTTP {e.code} {err_body}")
                except Exception as e:
                    errors.append(f"{item['sync_key']}: {e}")
        finally:
            conn.close()

        status = {
            "ok": len(errors) == 0,
            "synced_at": utc_now_iso(),
            "cycle": cfg.get("cycle", ""),
            "since": cfg.get("since", ""),
            "followed_remote_cycle": followed_cycle,
            "created": created,
            "updated": updated,
            "archived_legacy": archived_legacy,
            "errors": errors,
        }
        save_last_sync(status)
        if not quiet:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        return status


def cmd_sync_notion_direct(args: argparse.Namespace) -> None:
    cfg = load_config()
    run_direct_sync(
        cfg,
        force=args.force,
        quiet=args.quiet,
        cleanup_legacy=args.cleanup_legacy,
        skip_remote_check=args.no_follow,
    )


def cmd_reset(args: argparse.Namespace) -> None:
    """Start a brand-new Cycle without overwriting previous cycles in Notion."""
    cfg = load_config()
    old_cycle = str(cfg.get("cycle") or "")
    new_cycle = (args.name or "").strip() or default_cycle_name(existing_cycle=old_cycle)
    new_since = normalize_since(args.since) if args.since else utc_now_iso()

    cfg["cycle"] = new_cycle
    cfg["since"] = new_since
    save_config(cfg)

    sync_res = None
    if not args.no_sync:
        sync_res = run_direct_sync(cfg, force=True, quiet=True, skip_remote_check=True)

    print(
        json.dumps(
            {
                "previous_cycle": old_cycle,
                "new_cycle": new_cycle,
                "new_since": new_since,
                "sync": sync_res,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_backfill(_args: argparse.Namespace) -> None:
    cfg = load_config()
    conn = open_db()
    home = pathlib.Path.home()
    roots = [
        home / ".gemini" / "antigravity" / "brain",
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity-ide" / "brain",
    ]
    scanned_files = 0
    total_inserted = 0
    try:
        for root in roots:
            if not root.is_dir():
                continue
            for transcript in root.glob("*/.system_generated/logs/transcript.jsonl"):
                conv_id = transcript.parents[2].name
                scanned_files += 1
                total_inserted += ingest_transcript(conn, str(transcript), conv_id, cfg)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "scanned_transcripts": scanned_files,
                "new_events_imported": total_inserted,
                "cycle": cfg.get("cycle", ""),
                "since": cfg.get("since", ""),
                "db_path": str(DB_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def mask_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(cfg))
    tok = out.get("notion", {}).get("api_token", "")
    if tok:
        out["notion"]["api_token"] = f"{tok[:8]}...({len(tok)} chars)"
    return out


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    changed = False
    if args.member is not None:
        cfg["member"] = args.member.strip()
        changed = True
    if args.device is not None:
        cfg["device"] = args.device.strip()
        changed = True
    if args.cycle is not None:
        cfg["cycle"] = args.cycle.strip()
        changed = True
    if args.since is not None:
        cfg["since"] = normalize_since(args.since)
        changed = True
    if args.daily_limit is not None:
        cfg["daily_limit"] = max(0, int(args.daily_limit))
        changed = True
    if args.monthly_limit is not None:
        cfg["monthly_limit"] = max(0, int(args.monthly_limit))
        changed = True
    if args.warn_pct is not None:
        cfg["warn_threshold_pct"] = max(1, min(100, int(args.warn_pct)))
        changed = True
    if args.cooldown is not None:
        cfg["remind_cooldown_minutes"] = max(0, int(args.cooldown))
        changed = True
    if args.count_cache_read is not None:
        cfg["count_cache_read"] = args.count_cache_read.lower() in ("1", "true", "yes")
        changed = True
    if args.auto_sync is not None:
        cfg["auto_sync"] = args.auto_sync.lower() in ("1", "true", "yes")
        changed = True
    if args.auto_follow_reset is not None:
        cfg["auto_follow_reset"] = args.auto_follow_reset.lower() in ("1", "true", "yes")
        changed = True
    if args.notion_db_url is not None:
        cfg["notion"]["database_url"] = args.notion_db_url.strip()
        extracted = extract_notion_id(args.notion_db_url)
        if extracted:
            cfg["notion"]["database_id"] = extracted
        changed = True
    if args.notion_db_id is not None:
        cfg["notion"]["database_id"] = extract_notion_id(args.notion_db_id) or args.notion_db_id.strip()
        changed = True
    if args.notion_ds_id is not None:
        cfg["notion"]["data_source_id"] = args.notion_ds_id.strip()
        changed = True
    if args.notion_token is not None:
        cfg["notion"]["api_token"] = args.notion_token.strip()
        changed = True

    if changed:
        save_config(cfg)

    print(json.dumps(mask_config(cfg), ensure_ascii=False, indent=2))


def cmd_report(args: argparse.Namespace) -> None:
    cfg = load_config()
    conn = open_db()
    now_local = dt.datetime.now().astimezone()
    today_str = now_local.strftime("%Y-%m-%d")
    month_str = now_local.strftime("%Y-%m")
    week_start = (now_local - dt.timedelta(days=6)).strftime("%Y-%m-%d")
    s_sql, s_params = since_filter_sql(str(cfg.get("since") or ""))

    if args.period == "today":
        where_sql, params = f"local_date = ? AND ({s_sql})", (today_str, *s_params)
    elif args.period == "week":
        where_sql, params = f"local_date >= ? AND ({s_sql})", (week_start, *s_params)
    elif args.period == "month":
        where_sql, params = f"local_month = ? AND ({s_sql})", (month_str, *s_params)
    elif args.period == "since":
        where_sql, params = s_sql, s_params
    else:
        where_sql, params = "1 = 1", ()

    try:
        summary = query_period_totals(conn, where_sql, params)
        since_totals = query_period_totals(conn, s_sql, s_params)

        by_model = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT model,
                       SUM(input_tokens) AS input_tokens,
                       SUM(cache_read_tokens) AS cache_read_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(input_tokens + output_tokens) AS billable_tokens,
                       COUNT(*) AS calls
                FROM token_events
                WHERE {where_sql}
                GROUP BY model
                ORDER BY billable_tokens DESC
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()

    used = counted_tokens(since_totals, cfg)
    limit = int(cfg.get("monthly_limit") or cfg.get("daily_limit") or 0)

    last_sync = None
    if LAST_SYNC_PATH.exists():
        try:
            last_sync = json.loads(LAST_SYNC_PATH.read_text(encoding="utf-8"))
        except Exception:
            last_sync = None

    result = {
        "member": cfg["member"],
        "device": cfg["device"],
        "cycle": cfg.get("cycle", ""),
        "since": cfg.get("since", ""),
        "period": args.period,
        "progress": build_progress_bar(used, limit, since_totals["calls"]),
        "auto_sync": cfg.get("auto_sync", True),
        "auto_follow_reset": cfg.get("auto_follow_reset", True),
        "direct_sync_ready": bool(
            (cfg.get("notion") or {}).get("api_token")
            and (cfg.get("notion") or {}).get("database_id")
        ),
        "last_direct_sync": last_sync,
        "summary": summary,
        "since_totals": since_totals,
        "by_model": by_model,
        "notion": mask_config(cfg).get("notion", {}),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_notion_pending(args: argparse.Namespace) -> None:
    cfg = load_config()
    conn = open_db()
    try:
        pending = get_summary_pending_item(conn, cfg, force=args.force)
    finally:
        conn.close()

    print(
        json.dumps(
            {
                "notion": mask_config(cfg).get("notion", {}),
                "cycle": cfg.get("cycle", ""),
                "since": cfg.get("since", ""),
                "pending_count": len(pending),
                "items": pending,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_notion_mark_synced(args: argparse.Namespace) -> None:
    payload = json.loads(args.items_json)
    if isinstance(payload, dict):
        payload = [payload]
    conn = open_db()
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        for item in payload:
            conn.execute(
                """
                INSERT INTO notion_sync_log (
                    sync_key, notion_page_id,
                    last_synced_input, last_synced_cache_read,
                    last_synced_output, last_synced_calls, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sync_key) DO UPDATE SET
                    notion_page_id = excluded.notion_page_id,
                    last_synced_input = excluded.last_synced_input,
                    last_synced_cache_read = excluded.last_synced_cache_read,
                    last_synced_output = excluded.last_synced_output,
                    last_synced_calls = excluded.last_synced_calls,
                    updated_at = excluded.updated_at
                """,
                (
                    item["sync_key"],
                    item["notion_page_id"],
                    int(item.get("input_tokens") or 0),
                    int(item.get("cache_read_tokens") or 0),
                    int(item.get("output_tokens") or 0),
                    int(item.get("calls") or 0),
                    now_iso,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"marked_synced": len(payload)}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Antigravity Family Token Tracker")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("hook-stop")
    sub.add_parser("hook-pre-invocation")
    sub.add_parser("backfill")

    p_sync = sub.add_parser("sync-notion-direct")
    p_sync.add_argument("--force", action="store_true")
    p_sync.add_argument("--quiet", action="store_true")
    p_sync.add_argument("--cleanup-legacy", action="store_true")
    p_sync.add_argument("--no-follow", action="store_true")

    p_reset = sub.add_parser("reset")
    p_reset.add_argument("--name", help="Optional custom cycle name (e.g. '2026-12' or 'Cycle 2')")
    p_reset.add_argument("--since", help="Optional start timestamp/date (defaults to now)")
    p_reset.add_argument("--no-sync", action="store_true")

    p_cfg = sub.add_parser("config")
    p_cfg.add_argument("--member")
    p_cfg.add_argument("--device")
    p_cfg.add_argument("--cycle")
    p_cfg.add_argument("--since")
    p_cfg.add_argument("--daily-limit", type=int)
    p_cfg.add_argument("--monthly-limit", type=int)
    p_cfg.add_argument("--warn-pct", type=int)
    p_cfg.add_argument("--cooldown", type=int)
    p_cfg.add_argument("--count-cache-read")
    p_cfg.add_argument("--auto-sync")
    p_cfg.add_argument("--auto-follow-reset")
    p_cfg.add_argument("--notion-db-url")
    p_cfg.add_argument("--notion-db-id")
    p_cfg.add_argument("--notion-ds-id")
    p_cfg.add_argument("--notion-token")

    p_rep = sub.add_parser("report")
    p_rep.add_argument(
        "--period", choices=["since", "today", "week", "month", "all"], default="since"
    )

    p_pend = sub.add_parser("notion-pending")
    p_pend.add_argument("--force", action="store_true")

    p_mark = sub.add_parser("notion-mark-synced")
    p_mark.add_argument("--items-json", required=True)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handlers = {
        "hook-stop": cmd_hook_stop,
        "hook-pre-invocation": cmd_hook_pre_invocation,
        "backfill": cmd_backfill,
        "sync-notion-direct": cmd_sync_notion_direct,
        "reset": cmd_reset,
        "config": cmd_config,
        "report": cmd_report,
        "notion-pending": cmd_notion_pending,
        "notion-mark-synced": cmd_notion_mark_synced,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
