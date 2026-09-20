"""
The full-time horizon of pressure_observations: settled once, never revisited.

settle() writes a row as soon as EITHER horizon is known. goal_next_10 is known
ten minutes after the row, long before the whistle for anything observed before
~70', so those rows went out with final_goals NULL, and `pending` (settled_at
IS NULL) never selected them again. By 2026-09-14 that was 546k rows, 76% of
everything observed before 70'. They were the factory's next-goal rows: it
could not label them, and 17 of its trades could not settle.

complete_finals() is the repair and the ongoing rule. It takes the final from
the API, or from an API final already stored on another row of the same
fixture, and from the tape only as settle() would. These tests pin the rule and
the two properties this repo has paid for before: no network call inside a
write (db_txn), and no asking a key that has already refused (the 143k-call day).

    cd agent && source ../ingest/.venv/bin/activate && python -m pytest tests/test_pressure_final_horizon.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pressure_agent as pa  # noqa: E402

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pressure_agent.py")).read()


def _body(name: str) -> str:
    start = SRC.index(f"\ndef {name}(")
    # from start + 1: the slice begins with "\ndef ", which would find itself.
    return SRC[start:SRC.index("\ndef ", start + 1)]


# ── where a fixture's final comes from ───────────────────────────────────────

def test_the_api_answer_from_this_run_comes_first():
    assert pa._fixture_final(3, [2], pa.MAX_MINUTE, 5) == (3, "api")


def test_a_final_stored_on_a_later_row_completes_an_early_one():
    """The case that stranded 519k rows: the 55' row was settled at 65', and
    the 85' row of the same match got the API's final after the whistle."""
    assert pa._fixture_final(None, [2, 2], 60, 3) == (2, "api")


def test_two_stored_api_finals_that_disagree_resolve_nothing():
    """Two fixtures carry this on 2026-09-14 (4 vs 5, 5 vs 6). Picking one
    would be a guess. Nothing is written until the API is asked again."""
    assert pa._fixture_final(None, [4, 5], pa.MAX_MINUTE + 5, 5) == (None, None)


def test_the_tape_only_once_it_ran_past_max_minute():
    assert pa._fixture_final(None, None, pa.MAX_MINUTE, 2) == (2, "poll")
    assert pa._fixture_final(None, [], pa.MAX_MINUTE - 1, 2) == (None, None), (
        "a tape that stopped at 70' because the Mac slept is not a settled no-goal")


def test_no_tape_when_the_caller_withholds_it():
    # complete_finals(allow_tape=False) passes no last minute: the backfill
    # writes API finals only, so every row it touches is tagged and undoable.
    assert pa._fixture_final(None, [], None, 2) == (None, None)


# ── the API call ─────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _isolate_counter(monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTBALL_API_KEY", "test-key")
    monkeypatch.setattr(pa.af_budget, "_DIR", tmp_path)
    monkeypatch.setattr(pa.af_budget, "_process_counter", None)
    monkeypatch.setattr(pa.af_budget.atexit, "register", lambda *a, **k: None)


def test_a_refused_key_is_asked_once_and_espn_ids_never(monkeypatch, tmp_path):
    """2026-09-14: once the day's allowance was gone, every `ids=` batch came
    back with "Free plans do not have access to the Ids parameter". Asking
    the remaining batches spends nothing useful and teaches nothing."""
    _isolate_counter(monkeypatch, tmp_path)
    sent = []

    def refused(url, params=None, **kw):
        sent.append(params["ids"])
        return _Resp({"errors": {"plan": "Free plans do not have access to the Ids parameter."},
                      "response": []})

    monkeypatch.setattr(pa.requests, "get", refused)
    assert pa._final_goals_api([-400123456] + list(range(1, 61))) == {}
    assert len(sent) == 1, "the batches after a refusal must not be sent"
    assert "400123456" not in sent[0], "an ESPN id must never reach api-football"


def test_only_finished_fixtures_have_a_final(monkeypatch, tmp_path):
    _isolate_counter(monkeypatch, tmp_path)

    def answer(url, params=None, **kw):
        return _Resp({"errors": [], "response": [
            {"fixture": {"id": 1, "status": {"short": "FT"}},
             "score": {"fulltime": {"home": 2, "away": 1}}},
            {"fixture": {"id": 2, "status": {"short": "2H"}},
             "score": {"fulltime": {"home": None, "away": None}}},
        ]})

    monkeypatch.setattr(pa.requests, "get", answer)
    assert pa._final_goals_api([1, 2]) == {1: 3}


# ── the shape of the code ────────────────────────────────────────────────────

def test_complete_finals_fetches_everything_before_the_first_write():
    """The eight-minute idle transaction (db_txn) was a fetch inside a write
    loop. Every call here must come before the first UPDATE is sent."""
    body = _body("complete_finals")
    first_write = min(body.index("execute_values("), body.index("db_txn.atomic("))
    for fetch in ("_final_goals_api(", "_goal_minute_api("):
        calls = [i for i in range(len(body)) if body.startswith(fetch, i)]
        assert calls, f"complete_finals no longer calls {fetch} — update this test"
        assert all(i < first_write for i in calls), f"{fetch} runs after a write began"


def test_settle_pays_a_trade_only_on_the_api_final():
    """A tape final is what a refused key leaves, and a tape flap can only
    fabricate a WIN on an over (pt#5827)."""
    body = _body("settle")
    pay = body.index("UPDATE paper_trades")
    # The statement that opens the paying block, at its own indentation. A bare
    # "if " would find the ternary inside `won = (... if ... else ...)`.
    guard = body.rfind("\n                if ", 0, pay)
    assert body[guard:pay].lstrip().startswith('if final_src == "api"')


def test_settle_hands_its_early_rows_to_complete_finals_on_every_path():
    """Including the run with nothing pending, which is most quiet runs and is
    exactly when yesterday's early rows are waiting."""
    body = _body("settle")
    assert body.count("complete_finals(") >= 2
    assert "asked=" in body, "fixtures this run already asked must not be asked twice"


def test_a_tape_final_never_overwrites_anything():
    body = _body("complete_finals")
    tape_update = body[body.index("final_goals_source = 'poll'\n"):]
    tape_update = tape_update[:tape_update.index('"""')]
    assert "AND o.final_goals IS NULL" in tape_update
