// SPDX-License-Identifier: MIT
pragma solidity ^0.8.4;

import {ZeroHashes} from "./ZeroHashes.sol";


// ref https://github.com/zk-kit/zk-kit/pull/162
contract DepositContractWithProofs {
    uint256 internal constant _DEPOSIT_CONTRACT_TREE_DEPTH = 32;
    uint256 internal constant _MAX_DEPOSIT_COUNT = 2 ** _DEPOSIT_CONTRACT_TREE_DEPTH - 1;

    uint256 public depositCount;
    mapping(uint256 => bytes32) elements;

    function _indexForElement(uint8 level, uint256 index) internal pure returns (uint256) {
        // store the elements sparsely
        return _MAX_DEPOSIT_COUNT * level + index;
    }

    function deposit(bytes32 leaf) public {
        uint256 index = depositCount;
        require(index < _MAX_DEPOSIT_COUNT, "LazyIMT: tree is full");

        depositCount = index + 1;

        bytes32 hash = leaf;

        for (uint8 i = 0; ; ) {
            elements[_indexForElement(i, index)] = hash;
            // it's a left element so we don't hash until there's a right element
            if (index & 1 == 0) break;
            uint256 elementIndex = _indexForElement(i, index - 1);
            hash = keccak256(abi.encodePacked(elements[elementIndex], hash));
            unchecked {
                index >>= 1;
                i++;
            }
        }
    }

    function _update(bytes32 leaf, uint256 index) internal {
        uint256 numberOfLeaves = depositCount;
        require(index < numberOfLeaves, "LazyIMT: leaf must exist");

        bytes32 hash = leaf;

        for (uint8 i = 0; true; ) {
            elements[_indexForElement(i, index)] = hash;
            uint256 levelCount = numberOfLeaves >> (i + 1);
            if (levelCount <= index >> 1) break;
            if (index & 1 == 0) {
                uint256 elementIndex = _indexForElement(i, index + 1);
                hash = keccak256(abi.encodePacked(hash, elements[elementIndex]));
            } else {
                uint256 elementIndex = _indexForElement(i, index - 1);
                hash = keccak256(abi.encodePacked(elements[elementIndex], hash));
            }
            unchecked {
                index >>= 1;
                i++;
            }
        }
    }

    function root() public view returns (bytes32) {
        if (depositCount == 0) return ZeroHashes.defaultZero(_DEPOSIT_CONTRACT_TREE_DEPTH);

        bytes32[] memory levels = new bytes32[](_DEPOSIT_CONTRACT_TREE_DEPTH + 1);
        
        _levels(depositCount, _DEPOSIT_CONTRACT_TREE_DEPTH, levels);
        return levels[_DEPOSIT_CONTRACT_TREE_DEPTH];
    }

    function root(uint256 depositCount) public view returns (bytes32) {
        if (depositCount == 0) return ZeroHashes.defaultZero(_DEPOSIT_CONTRACT_TREE_DEPTH);
        bytes32[] memory levels = new bytes32[](_DEPOSIT_CONTRACT_TREE_DEPTH + 1);
        _levels(depositCount, _DEPOSIT_CONTRACT_TREE_DEPTH, levels);
        return levels[_DEPOSIT_CONTRACT_TREE_DEPTH];
    }

    function _levels(
        uint256 depositCount,
        uint256 depth,
        bytes32[] memory levels
    ) internal view {
        require(depositCount > 0, "LazyIMT: number of leaves must be > 0");
        // this should always short circuit if self.numberOfLeaves == 0
        uint256 index = depositCount - 1;

        if (index & 1 == 0) {
            levels[0] = elements[_indexForElement(0, index)];
        } else {
            levels[0] = ZeroHashes.defaultZero(0);
        }

        for (uint8 i = 0; i < depth; ) {
            if (index & 1 == 0) {
                levels[i + 1] = keccak256(abi.encodePacked(levels[i], ZeroHashes.defaultZero(i)));
            } else {
                uint256 levelCount = (depositCount) >> (i + 1);
                if (levelCount > index >> 1) {
                    bytes32 parent = elements[_indexForElement(i + 1, index >> 1)];
                    levels[i + 1] = parent;
                } else {
                    bytes32 sibling = elements[_indexForElement(i, index - 1)];
                    levels[i + 1] = keccak256(abi.encodePacked(sibling, levels[i]));
                }
            }
            unchecked {
                index >>= 1;
                i++;
            }
        }
    }

    function getMerkleProof(
        uint256 index,
        uint256 depositCount
    ) public view returns (bytes32[] memory) {
        
        // pass depth -1 because we don't need the root value
        bytes32[] memory _elements = new bytes32[](_DEPOSIT_CONTRACT_TREE_DEPTH);
        _levels(depositCount, _DEPOSIT_CONTRACT_TREE_DEPTH - 1, _elements);

        // unroll the bottom entry of the tree because it will never need to
        // be pulled from _levels
        if (index & 1 == 0) {
            if (index + 1 >= depositCount) {
                _elements[0] = ZeroHashes.defaultZero(0);
            } else {
                _elements[0] = elements[_indexForElement(0, index + 1)];
            }
        } else {
            _elements[0] = elements[_indexForElement(0, index - 1)];
        }
        index >>= 1;

        for (uint8 i = 1; i < _DEPOSIT_CONTRACT_TREE_DEPTH; ) {
            uint256 currentLevelCount = depositCount >> i;
            if (index & 1 == 0) {
                // if the element is an uncomputed edge node we'll use the value set
                // from _levels above
                // otherwise set as usual below
                if (index + 1 < currentLevelCount) {
                    _elements[i] = elements[_indexForElement(i, index + 1)];
                } else if (((depositCount - 1) >> i) <= index) {
                    _elements[i] = ZeroHashes.defaultZero(i);
                }
            } else {
                _elements[i] = elements[_indexForElement(i, index - 1)];
            }
            unchecked {
                index >>= 1;
                i++;
            }
        }
        return _elements;
    }

    function getMerkleProof(
        uint256 index
    ) public view returns (bytes32[] memory) {
        return getMerkleProof(index, depositCount);
    } 

    // Util. Just for tests to fake the depositCount
    function setDepositCount(uint40 newCount) external {
        depositCount = newCount;
    }
}