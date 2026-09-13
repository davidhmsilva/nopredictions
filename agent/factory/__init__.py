"""
The strategy factory — write strategies as JSON, backtest thousands of them out of
sample, and paper-trade the ones that pass on the live tape, ranked and promoted
by what they do forward.

    universes.py   the tapes: what a bot could see and pay at each moment, and what it paid
    spec.py        the strategy language (market, side, where, price, exit, stake)
    engine.py      masks, entries, returns net of the taker fee, train/test, FDR
    grids.py       templates that generate thousands of specs
    registry.py    factory_strategies / factory_trades (db/052)
    runner.py      live paper entries, settlement, promotion

CLI: agent/factory_cli.py
"""
