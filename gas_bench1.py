#!/usr/bin/env python3
import json
import os
import signal
import subprocess
import sys
import time
from statistics import mean
from pathlib import Path

from web3 import Web3
import numpy as np
import matplotlib.pyplot as plt

# -----------------------------
# Config
# -----------------------------
RPC_URL = "http://127.0.0.1:8545"
# Seconds between mined blocks on Anvil (0 = mine every tx instantly)
BLOCK_TIME = int(os.environ.get("BLOCK_TIME", "0"))
# How many funded accounts to use in parallel
NUM_ACCOUNTS = int(os.environ.get("NUM_ACCOUNTS", "20"))

ANVIL_CMD = ["anvil", "--silent"]
BUILD_CMD = ["forge", "build"]
# How many deposit calls per contract (override via env N=...)
N = int(os.environ.get("N", "25000"))
GAS_PRICE_GWEI = int(os.environ.get("GAS_GWEI", "1"))
SHOW_PLOTS = True   # set False if you only want files saved
OUT_DIR = Path("gas_out")
OUT_DIR.mkdir(exist_ok=True)

CONTRACTS = [
    {
        "name": "DepositContract",
        "artifact": "out/DepositContract.sol/DepositContract.json",
        "fn": "deposit",
        "args_factory": lambda w3, i: [w3.keccak(text=f"leaf-{i}")]
    },
    {
        "name": "DepositContractWithProofs",
        "artifact": "out/DepositContractWithProofs.sol/DepositContractWithProofs.json",
        "fn": "deposit",
        "args_factory": lambda w3, i: [w3.keccak(text=f"leaf-{i}")]
        # If your contract requires more args (e.g., proof arrays), change this lambda accordingly.
    }
]

# Default Anvil first account private key
ANVIL_PRIVKEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

# -----------------------------------------------------------------
# Accounts helpers (generate / fund extra accounts for parallel txs)
# -----------------------------------------------------------------

def prepare_accounts(w3, num_accounts: int):
    """Return a list of `num_accounts` funded local `Account` objects."""
    from eth_account import Account  # lazy import to keep top clean

    base = w3.eth.account.from_key(ANVIL_PRIVKEY)
    accounts = [base]

    # Create extra accounts deterministically (makes runs reproducible)
    for i in range(num_accounts - 1):
        # derive key by hashing base key with index (simple, not secure)
        key = Web3.keccak(text=f"extra-{i}")
        accounts.append(Account.from_key(key))

    # Fund new accounts with 100 ether each (if their balance is 0)
    funding_amount = w3.to_wei(100, "ether")
    nonce = w3.eth.get_transaction_count(base.address)
    for acct in accounts[1:]:
        if w3.eth.get_balance(acct.address) == 0:
            tx = {
                "to": acct.address,
                "value": funding_amount,
                "gas": 21000,
                "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
                "nonce": nonce,
            }
            signed = base.sign_transaction(tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
            nonce += 1
    # Wait for all funding txs to mine
    if num_accounts > 1:
        w3.eth.wait_for_transaction_receipt(w3.eth.get_block("latest").transactions[-1])

    return accounts


# ---------------------------------------------------------------
# Fast multi-account, fire-and-forget deposit runner
# ---------------------------------------------------------------

def call_deposit_many_multiacct(w3, contract, fn_name, args_factory, accounts, n_calls):
    """Pipeline `deposit` txs cycling through `accounts` to maximise throughput."""
    base_nonces = {acct.address: w3.eth.get_transaction_count(acct.address) for acct in accounts}

    tx_hashes = []
    gas_used = []

    # 1. Sign & send
    for i in range(n_calls):
        acct = accounts[i % len(accounts)]
        args = args_factory(w3, i)
        fn = getattr(contract.functions, fn_name)(*args)

        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": base_nonces[acct.address],
            #"gas": 300_000,
            "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        })
        base_nonces[acct.address] += 1

        signed = acct.sign_transaction(tx)
        txh = w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hashes.append(txh)

    # 2. Collect receipts
    for i, txh in enumerate(tx_hashes, 1):
        r = w3.eth.wait_for_transaction_receipt(txh)
        gas_used.append(r.gasUsed)
        # Print instantly when i is power of two (i.e., after 1,2,4,8,... inserts)
        if (i & (i - 1)) == 0:  # power of two check
            print(f"  ⚡ {fn_name} insert {i}: gas={r.gasUsed}")
        if i % max(1, n_calls // 10) == 0:
            print(f"  receipts {i}/{n_calls}")

    return gas_used, [h.hex() for h in tx_hashes]


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kwargs)


def start_anvil():
    print("🚀 Starting Anvil...")
    p = subprocess.Popen(ANVIL_CMD, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Give anvil time to boot and bind the RPC
    time.sleep(1.5)
    return p


def stop_process(p):
    if p and p.poll() is None:
        print("🛑 Stopping Anvil...")
        if sys.platform == "win32":
            p.terminate()
        else:
            os.kill(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()


def connect_web3():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise RuntimeError("Could not connect to Anvil RPC at 127.0.0.1:8545")
    return w3


def load_artifact(path):
    with open(path, "r") as f:
        return json.load(f)


def deploy(w3, artifact, acct):
    abi = artifact["abi"]
    # Foundry artifact may have bytecode in either "bytecode" or "bytecode.object"
    bytecode = artifact.get("bytecode", {}).get("object") or artifact.get("bytecode") or artifact["evm"]["bytecode"]["object"]

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = Contract.constructor().build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gas": 5_000_000,
        "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    addr = receipt.contractAddress
    return w3.eth.contract(address=addr, abi=abi)


def call_deposit_many(w3, contract, fn_name, args_factory, acct, n_calls):
    gas_list = []
    tx_hashes = []
    start_time = time.time()  # Track time for throughput calculation
    last_print_time = start_time
    last_print_i = 0

    for i in range(n_calls):
        args = args_factory(w3, i)
        fn = getattr(contract.functions, fn_name)(*args)

        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            #"gas": 300_000,
            "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        gas_list.append(receipt.gasUsed)
        tx_hashes.append(tx_hash.hex())
        # Real-time power-of-two−1 logging (i+1 because loop is 0-based)
        if ((i + 1) & i) == 0:  # power of two check on insert count (i+1)
            print(f"  ⚡ {fn_name} insert {i + 1}: gas={receipt.gasUsed}")

        # Light progress
        if (i + 1) % max(1, (n_calls // 10)) == 0:
            now = time.time()
            segment_elapsed = now - last_print_time if now > last_print_time else 0.0
            segment_calls = (i + 1) - last_print_i
            seg_rate = (segment_calls / segment_elapsed) if segment_elapsed > 0 else float('inf')
            total_elapsed = now - start_time if now > start_time else 0.0
            avg_rate = ((i + 1) / total_elapsed) if total_elapsed > 0 else float('inf')
            print(
                f"  {fn_name} {i+1}/{n_calls} (latest gas={receipt.gasUsed}) | "
                f"seg_rate={seg_rate:.2f} tx/s, avg_rate={avg_rate:.2f} tx/s"
            )
            last_print_time = now
            last_print_i = i + 1

    end_time = time.time()
    elapsed = end_time - start_time if end_time > start_time else 0.0
    if elapsed > 0:
        rate = n_calls / elapsed
        print(f"🚀 Completed {fn_name}: {n_calls} calls in {elapsed:.2f}s — {rate:.2f} inserts/s")

    return gas_list, tx_hashes


def print_stats(name, gas_values):
    arr = np.array(gas_values, dtype=np.int64)
    print(f"\n📊 {name} gas stats over {len(arr)} calls")
    print(f"  min:  {arr.min()}")
    print(f"  max:  {arr.max()}")
    print(f"  mean: {arr.mean():.2f}")
    print(f"  p95:  {np.percentile(arr, 95):.2f}")
    print(f"  p99:  {np.percentile(arr, 99):.2f}")


def save_csv(all_results):
    """
    all_results: dict[name] -> list of (i, gas, txhash)
    """
    out = OUT_DIR / "gas_report1.csv"
    with out.open("w") as f:
        f.write("contract,i,gas,txhash\n")
        for name, rows in all_results.items():
            for i, gas, txh in rows:
                f.write(f"{name},{i},{gas},{txh}\n")
    print(f"💾 Saved CSV: {out}")


def make_plots(data):
    """
    data: dict[name] -> dict{x: [i...], y: [gas...]}
    Creates two figures:
      1) Scatter comparing both series
      2) Boxplot of distributions
    Saves PNGs and optionally shows them.
    """
    # ---- Scatter ----
    plt.figure(figsize=(8, 6))
    for name, series in data.items():
        plt.scatter(series["x"], series["y"], label=name, alpha=0.7, s=40)
    plt.xlabel("Call index")
    plt.ylabel("Gas used")
    plt.title("Deposit Contracts Gas Cost (scatter)")
    plt.legend()
    plt.grid(True)
    scatter_path = OUT_DIR / "scatter.png"
    plt.savefig(scatter_path, dpi=160, bbox_inches="tight")
    print(f"🖼️  Saved {scatter_path}")
    if SHOW_PLOTS:
        plt.show()

    # ---- Boxplot ----
    names = list(data.keys())
    y_series = [data[n]["y"] for n in names]

    plt.figure(figsize=(7, 6))
    plt.boxplot(y_series, labels=names, notch=True, patch_artist=True)
    plt.ylabel("Gas used")
    plt.title("Gas Cost Distribution (box plots)")
    plt.grid(True, axis="y")

    # Add stats text above each box
    for i, name in enumerate(names, start=1):
        yvals = np.array(data[name]["y"])
        mean_v = yvals.mean()
        p95 = np.percentile(yvals, 95)
        p99 = np.percentile(yvals, 99)
        y_max = yvals.max()
        text = f"mean={mean_v:.1f}\n95%={p95:.1f}\n99%={p99:.1f}"
        plt.text(i, y_max * 1.02, text, ha="center", va="bottom", fontsize=9,
                 bbox=dict(facecolor="white", alpha=0.7, edgecolor="none"))

    box_path = OUT_DIR / "boxplot.png"
    plt.savefig(box_path, dpi=160, bbox_inches="tight")
    print(f"🖼️  Saved {box_path}")
    if SHOW_PLOTS:
        plt.show()


def main():
    # 1) Build
    print("📦 Compiling with forge...")
    run(BUILD_CMD)

    # 2) Start Anvil
    anvil = None
    try:
        anvil = start_anvil()

        # 3) Connect
        w3 = connect_web3()
        accounts = prepare_accounts(w3, NUM_ACCOUNTS)
        print(f"Using {len(accounts)} funded accounts. First: {accounts[0].address}")

        # 4) Deploy both
        deployed = {}
        for c in CONTRACTS:
            artifact = load_artifact(c["artifact"])
            print(f"🧱 Deploying {c['name']} ...")
            contract = deploy(w3, artifact, accounts[0]) # Deploy using the first account
            print(f"✅ {c['name']} at {contract.address}")
            deployed[c["name"]] = {
                "contract": contract,
                "fn": c["fn"],
                "args_factory": c["args_factory"]
            }

        # 5) Call deposit N times for each and collect gas
        all_results = {}
        plot_data = {}
        for name, obj in deployed.items():
            print(f"\n➡️  Running {name}.deposit(...) {N} times")
            gas_list, tx_hashes = call_deposit_many_multiacct(
                w3,
                obj["contract"],
                obj["fn"],
                obj["args_factory"],
                accounts,
                N,
            )
            print_stats(name, gas_list)

            # Store for CSV + plots
            rows = [(i, gas_list[i], tx_hashes[i]) for i in range(len(gas_list))]
            all_results[name] = rows
            plot_data[name] = {
                "x": list(range(len(gas_list))),
                "y": gas_list
            }

        # 6) Save CSV and make plots
        save_csv(all_results)
        make_plots(plot_data)

    finally:
        stop_process(anvil)


if __name__ == "__main__":
    main()
