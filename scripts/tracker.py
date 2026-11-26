#!/usr/bin/env python3
"""Family Token Tracker for Antigravity.

Tracks token consumption from Antigravity transcript.jsonl files into a local
SQLite database, emits gentle quota reminders in PreInvocation hooks, and
prepares daily aggregated records for syncing to a shared Notion database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import pathlib
import socket
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
DATA_DIR = pathlib.Path(
    os.environ.get("FAMILY_TOKEN_TRACKER_DATA")
    or (pathlib.Path.home() / ".gemini" / "antigravity" / "plugin_data" / "family-token-tracker")
)
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "usage.db"

DEFAULT_CONFIG: Dict[str, Any] = {
    "member": getpass.getuser(),
    "device": socket.gethostname().split(".")[0],
    "daily_limit": 0,
    "monthly_limit": 0,
    "count_cache_read": False,
    "warn_threshold_pct": 80,
    "remind_cooldown_minutes": 60,
    "notion": {
        "database_url": "",
        "data_source_id": "",
    },
}


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in raw.items():
        if k == "notion" and isinstance(v, dict):
            merged["notion"].update(v)
        else:
            merged[k] = v
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    ensure_data_dir()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_PATH)


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
    """Convert ISO8601 UTC timestamp to local (YYYY-MM-DD, YYYY-MM)."""
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


def ingest_transcript(
    conn: sqlite3.Connection,
    transcript_path: str,
    conversation_id: str,
    cfg: Dict[str, Any],
) -> int:
    """Parse transcript.jsonl and insert unseen PLANNER_RESPONSE token events."""
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
                created_at = str(entry.get("created_at") or "")
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

            now_local = dt.datetime.now().astimezone()
            today_str = now_local.strftime("%Y-%m-%d")
            month_str = now_local.strftime("%Y-%m")

            daily_totals = query_period_totals(conn, "local_date = ?", (today_str,))
            monthly_totals = query_period_totals(conn, "local_month = ?", (month_str,))

            daily_used = counted_tokens(daily_totals, cfg)
            monthly_used = counted_tokens(monthly_totals, cfg)

            daily_limit = int(cfg.get("daily_limit") or 0)
            monthly_limit = int(cfg.get("monthly_limit") or 0)
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
                "db_path": str(DB_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    changed = False
    if args.member is not None:
        cfg["member"] = args.member.strip()
        changed = True
    if args.device is not None:
        cfg["device"] = args.device.strip()
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
    if args.notion_db_url is not None:
        cfg["notion"]["database_url"] = args.notion_db_url.strip()
        changed = True
    if args.notion_ds_id is not None:
        cfg["notion"]["data_source_id"] = args.notion_ds_id.strip()
        changed = True

    if changed:
        save_config(cfg)

    print(json.dumps(cfg, ensure_ascii=False, indent=2))


def cmd_report(args: argparse.Namespace) -> None:
    cfg = load_config()
    conn = open_db()
    now_local = dt.datetime.now().astimezone()
    today_str = now_local.strftime("%Y-%m-%d")
    month_str = now_local.strftime("%Y-%m")
    week_start = (now_local - dt.timedelta(days=6)).strftime("%Y-%m-%d")

    if args.period == "today":
        where_sql, params = "local_date = ?", (today_str,)
    elif args.period == "week":
        where_sql, params = "local_date >= ?", (week_start,)
    elif args.period == "month":
        where_sql, params = "local_month = ?", (month_str,)
    else:
        where_sql, params = "1 = 1", ()

    try:
        summary = query_period_totals(conn, where_sql, params)
        today_totals = query_period_totals(conn, "local_date = ?", (today_str,))
        month_totals = query_period_totals(conn, "local_month = ?", (month_str,))

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

        by_day = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT local_date,
                       SUM(input_tokens) AS input_tokens,
                       SUM(cache_read_tokens) AS cache_read_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(input_tokens + output_tokens) AS billable_tokens,
                       COUNT(*) AS calls
                FROM token_events
                WHERE {where_sql}
                GROUP BY local_date
                ORDER BY local_date DESC
                LIMIT 31
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()

    daily_used = counted_tokens(today_totals, cfg)
    monthly_used = counted_tokens(month_totals, cfg)
    daily_limit = int(cfg.get("daily_limit") or 0)
    monthly_limit = int(cfg.get("monthly_limit") or 0)

    result = {
        "member": cfg["member"],
        "device": cfg["device"],
        "period": args.period,
        "count_cache_read": cfg["count_cache_read"],
        "quota": {
            "today_used": daily_used,
            "daily_limit": daily_limit,
            "daily_pct": round((daily_used / daily_limit) * 100, 1) if daily_limit else None,
            "month_used": monthly_used,
            "monthly_limit": monthly_limit,
            "monthly_pct": round((monthly_used / monthly_limit) * 100, 1) if monthly_limit else None,
        },
        "summary": summary,
        "by_model": by_model,
        "by_day": by_day,
        "notion": cfg.get("notion", {}),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_notion_pending(args: argparse.Namespace) -> None:
    cfg = load_config()
    conn = open_db()
    try:
        rows = conn.execute(
            """
            SELECT
                e.local_date,
                e.member,
                e.device,
                e.model,
                SUM(e.input_tokens) AS input_tokens,
                SUM(e.cache_read_tokens) AS cache_read_tokens,
                SUM(e.output_tokens) AS output_tokens,
                COUNT(*) AS calls
            FROM token_events e
            GROUP BY e.local_date, e.member, e.device, e.model
            ORDER BY e.local_date DESC, e.model ASC
            """
        ).fetchall()

        synced_map = {
            r["sync_key"]: dict(r)
            for r in conn.execute("SELECT * FROM notion_sync_log").fetchall()
        }
    finally:
        conn.close()

    pending: List[Dict[str, Any]] = []
    for r in rows:
        sync_key = f"{r['local_date']}|{r['member']}|{r['device']}|{r['model']}"
        in_tok = int(r["input_tokens"])
        cache_tok = int(r["cache_read_tokens"])
        out_tok = int(r["output_tokens"])
        calls = int(r["calls"])
        billable = in_tok + out_tok
        total = in_tok + cache_tok + out_tok

        prev = synced_map.get(sync_key)
        if prev and not args.force:
            if (
                prev["last_synced_input"] == in_tok
                and prev["last_synced_cache_read"] == cache_tok
                and prev["last_synced_output"] == out_tok
                and prev["last_synced_calls"] == calls
            ):
                continue

        pending.append(
            {
                "action": "update" if prev else "create",
                "sync_key": sync_key,
                "notion_page_id": prev["notion_page_id"] if prev else None,
                "properties": {
                    "Record": f"{r['local_date']} · {r['member']} · {r['model']}",
                    "date:Date:start": r["local_date"],
                    "date:Date:is_datetime": 0,
                    "Member": r["member"],
                    "Device": r["device"],
                    "Model": r["model"],
                    "Input Tokens": in_tok,
                    "Cache Read Tokens": cache_tok,
                    "Output Tokens": out_tok,
                    "Billable Tokens": billable,
                    "Total Tokens": total,
                    "Calls": calls,
                    "Sync Key": sync_key,
                },
                "raw": {
                    "input_tokens": in_tok,
                    "cache_read_tokens": cache_tok,
                    "output_tokens": out_tok,
                    "calls": calls,
                },
            }
        )

    print(
        json.dumps(
            {
                "notion": cfg.get("notion", {}),
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

    p_cfg = sub.add_parser("config")
    p_cfg.add_argument("--member")
    p_cfg.add_argument("--device")
    p_cfg.add_argument("--daily-limit", type=int)
    p_cfg.add_argument("--monthly-limit", type=int)
    p_cfg.add_argument("--warn-pct", type=int)
    p_cfg.add_argument("--cooldown", type=int)
    p_cfg.add_argument("--count-cache-read")
    p_cfg.add_argument("--notion-db-url")
    p_cfg.add_argument("--notion-ds-id")

    p_rep = sub.add_parser("report")
    p_rep.add_argument(
        "--period", choices=["today", "week", "month", "all"], default="month"
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
        "config": cmd_config,
        "report": cmd_report,
        "notion-pending": cmd_notion_pending,
        "notion-mark-synced": cmd_notion_mark_synced,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
