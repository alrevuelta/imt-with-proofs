pragma solidity ^0.8.13;

import "forge-std/Test.sol";

import {DepositContract} from "../src/DepositContract.sol";
import {DepositContractWithProofs} from "../src/DepositContractWithProofs.sol";

contract MerkleTest is Test {
    uint256 NUM_LEAVES_BENCH = 20000;

    function test_DepositContract() public {
        DepositContract depTree = new DepositContract();
        assertEq(depTree.root(), bytes32(0x27ae5ba08d7291c96c8cbddcc148bf48a6d68c7974b94356f53754ef6171d757));

        for (uint256 i = 0; i < 113; i++) {
            bytes32 leaf = bytes32(uint256(i));
            depTree.deposit(leaf);
        }

        assertEq(depTree.root(), bytes32(0xc18b8ae484ed343b731f3f257b3a87df6f781cdb4cda872fd6866bc2501affb8));
    }

    function test_DepositContractWithProofs() public {
        DepositContractWithProofs depTreeWithProofs = new DepositContractWithProofs();
        assertEq(depTreeWithProofs.root(), bytes32(0x27ae5ba08d7291c96c8cbddcc148bf48a6d68c7974b94356f53754ef6171d757));

        for (uint256 i = 0; i < 113; i++) {
            bytes32 leaf = bytes32(uint256(i));
            depTreeWithProofs.deposit(leaf);

        }

        assertEq(depTreeWithProofs.root(), bytes32(0xc18b8ae484ed343b731f3f257b3a87df6f781cdb4cda872fd6866bc2501affb8));

        for (uint256 i = 0; i < 113; i++) {
            bytes32[] memory proof = depTreeWithProofs.getMerkleProof(uint40(i));
            bytes32 leaf = bytes32(uint256(i));
            for (uint256 j = 0; j < proof.length; j++) {
                if ((i >> j) & 1 == 0) {
                    leaf = bytes32(keccak256(abi.encodePacked(leaf, proof[j])));
                } else {
                    leaf = bytes32(keccak256(abi.encodePacked(proof[j], leaf)));
                }
            }
            assertEq(leaf, depTreeWithProofs.root(), "Recomputed root mismatch");
        }
    }

    function test_DepositContractWithProofs_ArbitraryRoot() public {
        DepositContractWithProofs depTreeWithProofs = new DepositContractWithProofs();

        for (uint256 i = 0; i < 100; i++) {
            bytes32 leaf = bytes32(uint256(i));
            depTreeWithProofs.deposit(leaf);
        }

        bytes32 anotherRoot = depTreeWithProofs.root(50);

        assertEq(anotherRoot, bytes32(0x911071a80c7dca0f47bf741b4711b1b32210f0a3311a802660b67e93fa43ae6c));

        assertEq(depTreeWithProofs.root(), bytes32(0xb71c74ea4362589650bd0d65c4d544913b8ad8b31a4541e0f92389674ba24a72));

        // Generate proofs for subtrees. Eg the tree in reality has 100 leaves, but we generate proofs
        // for a subtree of just 50 leaves.
        for (uint256 i = 0; i < 50; i++) {
            bytes32[] memory proof = depTreeWithProofs.getMerkleProof(uint256(i), 50);
            bytes32 root = bytes32(uint256(i));
            for (uint256 j = 0; j < proof.length; j++) {
                if ((i >> j) & 1 == 0) {
                    root = bytes32(keccak256(abi.encodePacked(root, proof[j])));
                } else {
                    root = bytes32(keccak256(abi.encodePacked(proof[j], root)));
                }
            }
            assertEq(root, depTreeWithProofs.root(50), "Recomputed root mismatch");
        }
    }

    // Useless gas benchmarks. Highly biased by accessing the same storage slot over and over again.
    function testGas_DepositContract() public {
        DepositContract depTree = new DepositContract();
        
        for (uint256 i = 0; i < NUM_LEAVES_BENCH; i++) {
            vm.startSnapshotGas("bench");
            depTree.deposit("asd");

            uint256 gasUsed = vm.stopSnapshotGas();
            vm.resetGasMetering();
            
            vm.pauseGasMetering();
            console.log("DepositContractWithProofs", depTree.depositCount(), gasUsed);
            vm.resumeGasMetering();
        }
    }

    // Useless gas benchmarks. Highly biased by accessing the same storage slot over and over again.
    function testGas_DepositContractWithProofs() public {
        DepositContractWithProofs depTreeWithProofs = new DepositContractWithProofs();

        for (uint256 i = 0; i < NUM_LEAVES_BENCH; i++) {
            
            vm.startSnapshotGas("bench");
            depTreeWithProofs.deposit("asd");

            uint256 gasUsed = vm.stopSnapshotGas();
            vm.resetGasMetering();
            
            vm.pauseGasMetering();
            console.log("DepositContractWithProofs", depTreeWithProofs.depositCount(), gasUsed);
            vm.resumeGasMetering();
        }
    }
}
