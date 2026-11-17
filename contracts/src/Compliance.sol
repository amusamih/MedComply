// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "./Registration.sol";

interface IShipmentPermits {
    function parties(uint256 shipmentId) external view returns (address, address, bytes3, bytes3);
    function participants(uint256 shipmentId) external view returns (address, address);
    function jurisdictions(uint256 shipmentId) external view returns (bytes3, bytes3);
    function linkPermit(uint256 shipmentId, uint256 permitId, bool isExport) external;
    function registration() external view returns (Registration);
    function compliance() external view returns (address);
}

contract Compliance {
    enum Status { Missing, Pending, Approved, Revoked, Rejected }
    struct Permit { uint256 shipmentId; uint64 expiry; string documentCID; Status status; bytes32 reviewHash; }
    Registration public immutable registration;
    address public immutable INCB;
    IShipmentPermits public shipping;
    uint256 public nextExportId = 1;
    uint256 public nextImportId = 1;
    mapping(uint256 => Permit) private exportPermits;
    mapping(uint256 => Permit) private importPermits;
    mapping(uint16 => string) private quotaDocuments;
    event ShippingConnected(address indexed shipping);
    event PermitRequested(uint256 indexed shipmentId, uint256 indexed permitId, bool isExport, string documentCID);
    event PermitDecision(uint256 indexed shipmentId, uint256 indexed permitId, bool isExport, Status status, bytes32 reviewHash);
    event QuotaDocumentChanged(uint16 indexed year, string documentCID, bytes32 reviewHash);

    constructor(address reg) { registration = Registration(reg); INCB = registration.INCB(); }
    function connectShipping(address account) external {
        require(msg.sender == INCB && address(shipping) == address(0) && account.code.length > 0, "Connection forbidden");
        IShipmentPermits candidate = IShipmentPermits(account);
        require(address(candidate.registration()) == address(registration) && candidate.compliance() == address(this), "Mismatched contracts");
        shipping = candidate;
        emit ShippingConnected(account);
    }
    function setIncbQuotas(uint16 year, string calldata documentCID, bytes32 reviewHash) external {
        require(msg.sender == INCB && bytes(documentCID).length > 0 && reviewHash != bytes32(0), "Invalid quota publication");
        quotaDocuments[year] = documentCID;
        emit QuotaDocumentChanged(year, documentCID, reviewHash);
    }
    function getIncbCapIpfs(uint16 year) external view returns (string memory) { return quotaDocuments[year]; }
    function getPermit(uint256 id, bool isExport) public view returns (Permit memory) {
        return isExport ? exportPermits[id] : importPermits[id];
    }
    function requestPermit(uint256 shipmentId, bool isExport, string calldata documentCID) external returns (uint256 id) {
        require(address(shipping) != address(0) && bytes(documentCID).length > 0, "Missing connection or document");
        (address exporter, address importer) = shipping.participants(shipmentId);
        require(msg.sender == (isExport ? exporter : importer), "Wrong shipment participant");
        require(registration.isLicensed(msg.sender, isExport ? Registration.Role.Exporter : Registration.Role.Importer), "Unlicensed actor");
        id = isExport ? nextExportId++ : nextImportId++;
        Permit memory p = Permit(shipmentId, 0, documentCID, Status.Pending, bytes32(0));
        if (isExport) exportPermits[id] = p; else importPermits[id] = p;
        emit PermitRequested(shipmentId, id, isExport, documentCID);
    }
    function approvePermit(uint256 id, bool isExport, uint64 expiry, bytes32 reviewHash) external {
        Permit storage p = isExport ? exportPermits[id] : importPermits[id];
        (address exporter, address importer, bytes3 origin, bytes3 destination) = shipping.parties(p.shipmentId);
        require(p.status == Status.Pending && registration.isNCA(msg.sender, isExport ? origin : destination), "Unauthorized permit decision");
        require(registration.isLicensed(exporter, Registration.Role.Exporter) && registration.isLicensed(importer, Registration.Role.Importer), "Unlicensed party");
        require(registration.countryOf(exporter) == origin && registration.countryOf(importer) == destination, "Party jurisdiction changed");
        require(expiry > block.timestamp && reviewHash != bytes32(0), "Invalid expiry or review");
        p.expiry = expiry; p.status = Status.Approved; p.reviewHash = reviewHash;
        emit PermitDecision(p.shipmentId, id, isExport, Status.Approved, reviewHash);
        shipping.linkPermit(p.shipmentId, id, isExport);
    }
    function revokePermit(uint256 id, bool isExport, bytes32 reviewHash) external {
        Permit storage p = isExport ? exportPermits[id] : importPermits[id];
        (bytes3 origin, bytes3 destination) = shipping.jurisdictions(p.shipmentId);
        require(p.status == Status.Approved && registration.isNCA(msg.sender, isExport ? origin : destination) && reviewHash != bytes32(0), "Unauthorized revocation");
        p.status = Status.Revoked; p.reviewHash = reviewHash;
        emit PermitDecision(p.shipmentId, id, isExport, Status.Revoked, reviewHash);
    }
    function rejectPermit(uint256 id, bool isExport, bytes32 reviewHash) external {
        Permit storage p = isExport ? exportPermits[id] : importPermits[id];
        (bytes3 origin, bytes3 destination) = shipping.jurisdictions(p.shipmentId);
        require(p.status == Status.Pending && registration.isNCA(msg.sender, isExport ? origin : destination) && reviewHash != bytes32(0), "Unauthorized rejection");
        p.status = Status.Rejected; p.reviewHash = reviewHash;
        emit PermitDecision(p.shipmentId, id, isExport, Status.Rejected, reviewHash);
    }
    function isValid(uint256 id, uint256 shipmentId, bool isExport) external view returns (bool) {
        Permit memory p = getPermit(id, isExport);
        return p.status == Status.Approved && p.shipmentId == shipmentId && p.expiry >= block.timestamp;
    }
}
