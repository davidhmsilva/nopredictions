"""
Database tools for the NOPREDICTIONS agent.

All DB interaction goes through here. Read-only functions for context,
write functions for logging trades and agent runs.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '../../ingest/.env'))

DATABASE_URL = os.getenv('DATABASE_URL')


def _conn():
    return psycopg2.connect(DATABASE_URL)


get_conn = _conn


def _serial(obj):
    """JSON serialiser for dates/decimals returned by psycopg2."""
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if hasattr(obj, '__float__'):
        return float(obj)
    raise TypeError(f"Not serialisable: {type(obj)}")


def _rows(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# CONTEXT — what agents read
# ---------------------------------------------------------------------------

def get_leagues() -> list[dict]:
    """Return all leagues with match counts."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT l.code, l.name, l.country, COUNT(m.id) AS match_count
            FROM leagues l
            LEFT JOIN seasons s ON s.league_id = l.id
            LEFT JOIN matches m ON m.season_id = s.id
            GROUP BY l.code, l.name, l.country
            ORDER BY match_count DESC
        """)
        return _rows(cur)


def get_active_strategies() -> list[dict]:
    """Return all active strategies."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.name, s.rules, s.promoted_at
            FROM strategies s
            WHERE s.retired_at IS NULL
            ORDER BY s.promoted_at DESC
        """)
        return _rows(cur)


def get_upcoming_matches(days_ahead: int = 3) -> list[dict]:
    """Return upcoming matches in the next N days with available odds."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                m.id AS match_id,
                l.name AS league,
                l.code AS league_code,
                th.canonical_name AS home_team,
                ta.canonical_name AS away_team,
                m.kickoff_utc,
                mo_open.home_odds  AS pinnacle_open_home,
                mo_open.draw_odds  AS pinnacle_open_draw,
                mo_open.away_odds  AS pinnacle_open_away
            FROM matches m
            JOIN seasons s   ON s.id = m.season_id
            JOIN leagues l   ON l.id = s.league_id
            JOIN teams th    ON th.id = m.home_team_id
            JOIN teams ta    ON ta.id = m.away_team_id
            LEFT JOIN match_odds mo_open ON mo_open.match_id = m.id
                AND mo_open.bookmaker_id = (
                    SELECT id FROM bookmakers WHERE name = 'Pinnacle (legacy)'
                )
            WHERE m.kickoff_utc BETWEEN NOW() AND NOW() + INTERVAL '%s days'
              AND m.home_score IS NULL
            ORDER BY m.kickoff_utc
        """, (days_ahead,))
        return _rows(cur)


# ---------------------------------------------------------------------------
# QUERY — read-only analysis
# ---------------------------------------------------------------------------

def run_analysis_query(sql: str) -> list[dict]:
    """
    Execute a read-only SELECT query and return results as list of dicts.
    """
    stripped = sql.strip().upper()
    if not stripped.startswith('SELECT') and not stripped.startswith('WITH'):
        raise ValueError("Only SELECT/WITH queries allowed in run_analysis_query")

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return _rows(cur)


# ---------------------------------------------------------------------------
# WRITE — agent outputs
# ---------------------------------------------------------------------------

def log_agent_run(
    agent_name: str,
    action: str,
    summary: str,
    metadata: dict | None = None,
) -> None:
    """Log an agent invocation for audit trail."""
    run_type = f"{agent_name}:{action}"
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO agent_runs
                (run_type, started_at, finished_at, input_context, output_summary, status)
            VALUES (%s, NOW(), NOW(), %s, %s, 'completed')
        """, (
            run_type,
            json.dumps(metadata or {}, default=_serial),
            summary,
        ))
        conn.commit()
