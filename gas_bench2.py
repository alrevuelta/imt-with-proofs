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
ANVIL_CMD = ["anvil", "--silent"]
BUILD_CMD = ["forge", "build"]
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

    for i in range(n_calls):
        args = args_factory(w3, i)
        fn = getattr(contract.functions, fn_name)(*args)

        tx = fn.build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 300_000,
            "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        gas_list.append(receipt.gasUsed)
        tx_hashes.append(tx_hash.hex())

        # Light progress
        if (i + 1) % max(1, (n_calls // 10)) == 0:
            print(f"  {fn_name} {i+1}/{n_calls} (latest gas={receipt.gasUsed})")

    end_time = time.time()
    elapsed = end_time - start_time if end_time > start_time else 0.0
    if elapsed > 0:
        rate = n_calls / elapsed
        print(f"🚀 Completed {fn_name}: {n_calls} calls in {elapsed:.2f}s — {rate:.2f} inserts/s")

    return gas_list, tx_hashes


# -------------------------------------------------
# New helper: set depositCount to 2^k-1 then deposit
# -------------------------------------------------

POWERS = 28  # maximum exponent k (tests 2^1-1 up to 2^POWERS-1)


def call_deposit_after_precounts(w3, contract, fn_name, args_factory, acct, max_power):
    """
    For k in [1, max_power] inclusive, this will:
      1) contract.setDepositCount(2**k - 1)
      2) Perform a single deposit

    Returns two parallel lists: gas_used for each deposit and the tx hashes.
    """
    gas_list = []
    tx_hashes = []

    set_fn = contract.functions.setDepositCount

    for k in range(1, max_power + 1):
        new_count = 2 ** k - 1

        # 1) setDepositCount
        tx = set_fn(new_count).build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            #"gas": 200_000,
            "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        })
        signed = acct.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(
                f"setDepositCount failed for k={k}, new_count={new_count}."
            )

        # 2) One deposit after modifying depositCount
        args = args_factory(w3, k)  # reuse k as index for unique leaf
        fn = getattr(contract.functions, fn_name)(*args)
        tx2 = fn.build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            #"gas": 300_000,
            "gasPrice": w3.to_wei(GAS_PRICE_GWEI, "gwei"),
        })
        signed2 = acct.sign_transaction(tx2)
        tx_hash2 = w3.eth.send_raw_transaction(signed2.raw_transaction)
        receipt2 = w3.eth.wait_for_transaction_receipt(tx_hash2)
        if receipt2.status != 1:
            raise RuntimeError(
                f"deposit failed for k={k}, new_count={new_count}. Receipt status=0"
            )

        gas_list.append(receipt2.gasUsed)
        tx_hashes.append(tx_hash2.hex())

        print(f"  k={k:2d}, set depositCount to {new_count}, gas={receipt2.gasUsed}")

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
    out = OUT_DIR / "gas_report2.csv"
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
    plt.xscale("linear")
    plt.yscale("linear")
    plt.xlabel("Depth exponent k (where count=2^k-1)")
    plt.ylabel("Gas used")
    plt.title("Deposit Contracts Gas Cost (scatter)")
    plt.legend()
    plt.grid(True)
    scatter_path = OUT_DIR / "scatter2.png"
    plt.ticklabel_format(style="plain", axis="both")
    # Use ticks/labels from the first series for the x-axis
    first_series = next(iter(data.values()))
    ticks = first_series["x"]
    labels = first_series["labels"]
    plt.xticks(ticks, labels, rotation=45, ha="right")
    plt.savefig(scatter_path, dpi=160, bbox_inches="tight")
    print(f"🖼️  Saved {scatter_path}")
    if SHOW_PLOTS:
        plt.show()
    return  # Removed boxplot whiskers plot


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
        acct = w3.eth.account.from_key(ANVIL_PRIVKEY)
        print(f"Using account: {acct.address}")

        # 4) Deploy both
        deployed = {}
        for c in CONTRACTS:
            artifact = load_artifact(c["artifact"])
            print(f"🧱 Deploying {c['name']} ...")
            contract = deploy(w3, artifact, acct)
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
            print(f"\n➡️  Running {name}: set depositCount to 2^k-1 then deposit (k=1..{POWERS})")
            gas_list, tx_hashes = call_deposit_after_precounts(
                w3,
                obj["contract"],
                obj["fn"],
                obj["args_factory"],
                acct,
                POWERS,
            )
            print_stats(name, gas_list)

            # Store for CSV + plots
            rows = [(i, gas_list[i], tx_hashes[i]) for i in range(len(gas_list))]
            all_results[name] = rows
            exponents = list(range(1, len(gas_list) + 1))
            plot_data[name] = {
                "x": exponents,  # linear spacing 1..k
                "y": gas_list,
                "labels": [f"{2 ** k - 1}\n(2^{k}-1)" for k in exponents],
            }

        # 6) Save CSV and make plots
        save_csv(all_results)
        make_plots(plot_data)

    finally:
        stop_process(anvil)


if __name__ == "__main__":
    main()
