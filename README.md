# imt-proofs

Proof of concept containing two contracts with a fixed-depth incremental only merkle tree:
* [DepositContract.sol](src/DepositContract.sol): Very gas efficient, stores just the frontier and lazily calculates the root.
* [DepositContractWithProofs.sol](src/DepositContractWithProofs.sol): Less gas efficient, stores intermediate leaves and allows to calculate merkle proofs of arbitrary leaves.

Inspired by [this](https://github.com/agglayer/agglayer-contracts/blob/main/contracts/lib/DepositContract.sol) and [this](https://github.com/zk-kit/zk-kit/pull/162).

Use at your own risk.

Run tests:
```
forge test
```

Run gas benchmarks 25k leaves.
```
python gas_bench1.py
```


Run gas benchmarks for leaves at indexes `2^x`.
```
python gas_bench2.py
```