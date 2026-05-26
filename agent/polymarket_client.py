"""
Polymarket CLOB v2 client wrapper.

Architecture (email-signup account):
  - EOA signer:        POLYMARKET_PRIVATE_KEY → signs orders, no funds held here
  - Deposit wallet:    PM_DEPOSIT_WALLET       → ERC-1271 smart account, holds collateral + allowances
  - signature_type:    POLY_1271 (=3)

Usage:
    python polymarket_client.py test                                 # auth + balance
    python polymarket_client.py orders                               # list open orders
    python polymarket_client.py buy <token_id> <price> <size>        # GTC limit BUY
    python polymarket_client.py sell <token_id> <price> <size>       # GTC limit SELL
    python polymarket_client.py cancel <order_id>
"""
import os
import sys
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

ENV_PATH = Path(__file__).parent.parent / "ingest" / ".env"
load_dotenv(ENV_PATH)

PK = os.environ.get("POLYMARKET_PRIVATE_KEY")
DEPOSIT = os.environ.get("PM_DEPOSIT_WALLET")

if not PK or not DEPOSIT:
    print("❌ POLYMARKET_PRIVATE_KEY and PM_DEPOSIT_WALLET required in ingest/.env")
    sys.exit(1)

from py_clob_client_v2 import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
    SignatureTypeV2,
)

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIG_TYPE = SignatureTypeV2.POLY_1271

# Hard caps to prevent runaway orders. Bypass with explicit flag in callers.
MAX_NOTIONAL_PER_ORDER = float(os.environ.get("PM_MAX_NOTIONAL_PER_ORDER", "5.0"))


# ---- client construction ----

_creds_cache: ApiCreds | None = None
_client_cache: ClobClient | None = None


def _derive_creds() -> ApiCreds:
    global _creds_cache
    if _creds_cache is None:
        c = ClobClient(host=HOST, chain_id=CHAIN_ID, key=PK)
        _creds_cache = c.create_or_derive_api_key()
    return _creds_cache


def get_client() -> ClobClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = ClobClient(
            host=HOST,
            chain_id=CHAIN_ID,
            key=PK,
            creds=_derive_creds(),
            signature_type=SIG_TYPE,
            funder=DEPOSIT,
        )
    return _client_cache


# ---- read helpers ----

def get_balance() -> float:
    """Returns collateral balance in dollar units (e.g. 20.0)."""
    bal = get_client().get_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    return int(bal["balance"]) / 1e6


def get_market_meta(token_id: str) -> dict:
    """
    Looks up tick_size and neg_risk for a token via /tick-size + /neg-risk endpoints.
    Both are required to build a valid order.
    """
    c = get_client()
    tick = c.get_tick_size(token_id)
    neg_risk = c.get_neg_risk(token_id)
    return {"tick_size": str(tick), "neg_risk": bool(neg_risk)}


# ---- write methods ----

def place_limit_order(
    token_id: str,
    side: str,
    price: float,
    size: float,
    order_type: str = "GTC",
    *,
    allow_large: bool = False,
) -> dict:
    """
    Resting limit order. Default GTC (good-til-cancelled).

    Args:
        token_id:  PM outcome token (string of decimal-encoded uint256)
        side:      "BUY" or "SELL"
        price:     0.0 < price < 1.0 (probability)
        size:      shares (not USDC) — notional = price * size for BUY, size for SELL
        order_type: "GTC", "GTD", "FOK", "FAK"
        allow_large: bypass MAX_NOTIONAL_PER_ORDER cap

    Returns CLOB response dict.
    """
    side_e = Side.BUY if side.upper() == "BUY" else Side.SELL
    notional = price * size if side_e == Side.BUY else size  # rough — SELL receives `size * price`
    if not allow_large and notional > MAX_NOTIONAL_PER_ORDER:
        raise ValueError(
            f"Notional ${notional:.2f} exceeds cap ${MAX_NOTIONAL_PER_ORDER:.2f}. "
            f"Pass allow_large=True to bypass."
        )

    meta = get_market_meta(token_id)
    opts = PartialCreateOrderOptions(tick_size=meta["tick_size"], neg_risk=meta["neg_risk"])
    args = OrderArgs(token_id=token_id, price=price, size=size, side=side_e)
    return get_client().create_and_post_order(
        order_args=args, options=opts, order_type=getattr(OrderType, order_type)
    )


def cancel_order(order_id: str) -> Any:
    from py_clob_client_v2 import OrderPayload
    return get_client().cancel_order(OrderPayload(orderID=order_id))


def cancel_all() -> Any:
    return get_client().cancel_all()


def get_open_orders(market: str | None = None) -> Any:
    return get_client().get_open_orders(market=market) if market else get_client().get_open_orders()


# ---- CLI ----

def _cmd_test():
    from eth_account import Account
    eoa = Account.from_key(PK).address
    print(f"🔑 EOA signer:     {eoa}")
    print(f"💼 Deposit wallet: {DEPOSIT}")
    print(f"🖋️  signature_type: POLY_1271")
    print(f"💰 CLOB balance:   ${get_balance():.2f}")
    bal = get_client().get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
    print("   Allowances:")
    for spender, amt in bal["allowances"].items():
        v = int(amt)
        print(f"     {spender}: {'MAX' if v > 10**30 else f'{v/1e6:.2f}'}")
    print(f"⛔ Max notional per order: ${MAX_NOTIONAL_PER_ORDER:.2f}")


def _cmd_orders():
    orders = get_open_orders()
    print(orders)


def _cmd_buy_sell(side: str, argv: list[str]):
    if len(argv) < 3:
        print(f"Usage: {side.lower()} <token_id> <price> <size> [--force]")
        sys.exit(2)
    tok, price, size = argv[0], float(argv[1]), float(argv[2])
    force = "--force" in argv
    resp = place_limit_order(tok, side, price, size, allow_large=force)
    print(resp)


def _cmd_cancel(argv: list[str]):
    if not argv:
        print("Usage: cancel <order_id>")
        sys.exit(2)
    print(cancel_order(argv[0]))


def _cmd_place_json(argv: list[str]):
    """
    Subprocess-friendly command for live_executor.
    Args: <token_id> <side BUY|SELL> <price> <size>
    Prints a single-line JSON envelope on stdout.
    """
    import json as _json
    import traceback
    if len(argv) < 4:
        print(_json.dumps({"ok": False, "error": "usage: place_json <token> <side> <price> <size>"}))
        sys.exit(2)
    try:
        tok, side, price, size = argv[0], argv[1], float(argv[2]), float(argv[3])
        resp = place_limit_order(tok, side, price, size, allow_large=True)
        print(_json.dumps({"ok": True, "resp": resp}, default=str))
    except Exception as e:
        print(_json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}))
        sys.exit(1)


def _cmd_balance_json():
    import json as _json
    try:
        print(_json.dumps({"ok": True, "balance_usd": get_balance()}))
    except Exception as e:
        print(_json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def _cmd_get_order_json(argv: list[str]):
    """Subprocess-friendly: print status of a single order as JSON."""
    import json as _json
    if not argv:
        print(_json.dumps({"ok": False, "error": "usage: get_order_json <order_id>"}))
        sys.exit(2)
    try:
        r = get_client().get_order(argv[0])
        print(_json.dumps({"ok": True, "order": r}, default=str))
    except Exception as e:
        print(_json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


def _cmd_cancel_json(argv: list[str]):
    import json as _json
    if not argv:
        print(_json.dumps({"ok": False, "error": "usage: cancel_json <order_id>"}))
        sys.exit(2)
    try:
        r = cancel_order(argv[0])
        print(_json.dumps({"ok": True, "resp": r}, default=str))
    except Exception as e:
        print(_json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    args = sys.argv[2:]
    if cmd in ("test", "balance"):
        _cmd_test()
    elif cmd == "orders":
        _cmd_orders()
    elif cmd == "buy":
        _cmd_buy_sell("BUY", args)
    elif cmd == "sell":
        _cmd_buy_sell("SELL", args)
    elif cmd == "cancel":
        _cmd_cancel(args)
    elif cmd == "place_json":
        _cmd_place_json(args)
    elif cmd == "balance_json":
        _cmd_balance_json()
    elif cmd == "get_order_json":
        _cmd_get_order_json(args)
    elif cmd == "cancel_json":
        _cmd_cancel_json(args)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
