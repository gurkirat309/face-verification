// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title EvidenceLedger
 * @notice Tamper-evident on-chain anchor ledger for face verification & evidence records.
 * Stores only cryptographic hashes (bytes32). Never raw biometric data or embeddings.
 */
contract EvidenceLedger {

    struct Anchor {
        bytes32 bundleHash;
        bytes32 subjectHash;
        uint256 timestamp;
    }

    // subjectHash (bytes32) -> consent granted (bool)
    mapping(bytes32 => bool) private _consent;

    // recordId (string) -> Anchor struct
    mapping(string => Anchor) private _anchors;

    // bundleHash (bytes32) -> recordId (string) [for idempotency checks]
    mapping(bytes32 => string) private _bundleToRecordId;

    event ConsentRegistered(bytes32 indexed subjectHash, bool flag, uint256 timestamp);
    event EvidenceAnchored(string recordId, bytes32 indexed bundleHash, bytes32 indexed subjectHash, uint256 timestamp);

    /**
     * @notice Record or update consent status for a subject hash.
     * @param subjectHash 32-byte salted SHA-256 hash of subject face embedding.
     * @param flag True to grant consent, False to withdraw.
     */
    function registerConsent(bytes32 subjectHash, bool flag) external {
        _consent[subjectHash] = flag;
        emit ConsentRegistered(subjectHash, flag, block.timestamp);
    }

    /**
     * @notice Check current consent status for a subject hash.
     * @param subjectHash 32-byte salted SHA-256 hash of subject.
     */
    function isConsented(bytes32 subjectHash) external view returns (bool) {
        return _consent[subjectHash];
    }

    /**
     * @notice Anchor an evidence bundle hash on-chain.
     * @param recordId Unique string identifier of the record (e.g. UUID).
     * @param bundleHash 32-byte SHA-256 hash of evidence record content.
     * @param subjectHash 32-byte salted subject identifier hash.
     */
    function anchorEvidence(
        string calldata recordId,
        bytes32 bundleHash,
        bytes32 subjectHash
    ) external {
        require(bundleHash != bytes32(0), "Bundle hash cannot be empty");
        require(bytes(recordId).length > 0, "Record ID cannot be empty");

        _anchors[recordId] = Anchor({
            bundleHash: bundleHash,
            subjectHash: subjectHash,
            timestamp: block.timestamp
        });

        _bundleToRecordId[bundleHash] = recordId;

        emit EvidenceAnchored(recordId, bundleHash, subjectHash, block.timestamp);
    }

    /**
     * @notice Read anchored details for a record ID.
     */
    function getAnchor(string calldata recordId)
        external
        view
        returns (
            bytes32 bundleHash,
            bytes32 subjectHash,
            uint256 timestamp
        )
    {
        Anchor memory a = _anchors[recordId];
        return (a.bundleHash, a.subjectHash, a.timestamp);
    }

    /**
     * @notice Lookup record ID by bundle hash (used for idempotency).
     */
    function getRecordIdByBundle(bytes32 bundleHash)
        external
        view
        returns (string memory)
    {
        return _bundleToRecordId[bundleHash];
    }
}
