"""
Approve Polymarket CLOB exchange contracts to spend our USDC.e and CTF tokens.
One-time setup. After this, the CLOB sees the EOA's USDC.e balance and can match orders.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(Path(__file__).parent.parent / "ingest" / ".env")

PK = os.environ["POLYMARKET_PRIVATE_KEY"]
acct = Account.from_key(PK)
ADDR = acct.address
print(f"🔑 EOA: {ADDR}")

RPC = "https://polygon.drpc.org"
w3 = Web3(Web3.HTTPProvider(RPC))
from web3.middleware import ExtraDataToPOAMiddleware
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
assert w3.is_connected(), "RPC down"

USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
CTF    = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")

SPENDERS = [
    ("Exchange",        "0xE111180000d2663C0091e4f400237545B87B996B"),
    ("NegRisk CTF Adp", "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"),
    ("NegRisk Exchange","0xe2222d279d744050d28e00520010520000310F59"),
]

MAX_UINT = 2**256 - 1

ERC20_ABI = [{
    "name": "approve", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
    "outputs": [{"type": "bool"}],
}, {
    "name": "allowance", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
    "outputs": [{"type": "uint256"}],
}]
CTF_ABI = [{
    "name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
    "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
    "outputs": [],
}, {
    "name": "isApprovedForAll", "type": "function", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}, {"name": "operator", "type": "address"}],
    "outputs": [{"type": "bool"}],
}]

usdc = w3.eth.contract(address=USDC_E, abi=ERC20_ABI)
ctf  = w3.eth.contract(address=CTF, abi=CTF_ABI)


def send(tx):
    tx["nonce"] = w3.eth.get_transaction_count(ADDR)
    tx["chainId"] = 137
    tx["from"] = ADDR
    if "gas" not in tx:
        tx["gas"] = 100_000
    # EIP-1559
    base = w3.eth.get_block("latest")["baseFeePerGas"]
    tx["maxPriorityFeePerGas"] = w3.to_wei(30, "gwei")
    tx["maxFeePerGas"] = base * 2 + tx["maxPriorityFeePerGas"]
    signed = w3.eth.account.sign_transaction(tx, PK)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    return h.hex()


def approve_all(dry_run=False):
    print("\n=== USDC.e allowances ===")
    for label, spender in SPENDERS:
        spender = Web3.to_checksum_address(spender)
        current = usdc.functions.allowance(ADDR, spender).call()
        if current >= MAX_UINT // 2:
            print(f"  ✅ {label:18s} already approved")
            continue
        print(f"  ⏳ {label:18s} approving (current={current})...")
        if dry_run:
            continue
        tx = usdc.functions.approve(spender, MAX_UINT).build_transaction({"from": ADDR})
        h = send(tx)
        print(f"     tx: 0x{h}")
        r = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        print(f"     {'✅ mined' if r.status == 1 else '❌ FAILED'} block {r.blockNumber}")

    print("\n=== CTF setApprovalForAll ===")
    for label, op in SPENDERS:
        op = Web3.to_checksum_address(op)
        if ctf.functions.isApprovedForAll(ADDR, op).call():
            print(f"  ✅ {label:18s} already approved")
            continue
        print(f"  ⏳ {label:18s} approving...")
        if dry_run:
            continue
        tx = ctf.functions.setApprovalForAll(op, True).build_transaction({"from": ADDR})
        h = send(tx)
        print(f"     tx: 0x{h}")
        r = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        print(f"     {'✅ mined' if r.status == 1 else '❌ FAILED'} block {r.blockNumber}")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    if dry:
        print("[DRY RUN — no tx sent]")
    approve_all(dry_run=dry)
    print("\nDone. Run `python polymarket_client.py balance` and re-check CLOB balance.")
