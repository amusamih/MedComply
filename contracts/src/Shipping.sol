// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "./Registration.sol";
import "./Compliance.sol";

contract Shipping {
    enum State { Missing, Created, ExportCleared, Approved, InTransit, ImportCleared, Received, Cancelled }
    struct Shipment {
        address exporter; address importer; address logistics;
        bytes3 origin; bytes3 destination; bytes32 substance;
        uint256 quantity; uint16 year; string documentCID;
        uint256 exportPermit; uint256 importPermit; State state;
    }
    struct Quota { uint256 nationalCap; uint256 globalCap; uint256 used; uint256 reserved; bool nationalSet; bool globalSet; }
    Registration public immutable registration;
    Compliance public immutable compliance;
    address public immutable INCB;
    uint256 public nextShipmentId = 1;
    mapping(uint256 => Shipment) private shipments;
    mapping(bytes32 => Quota) public quotas;
    // All quantities are integer micrograms; no floating-point or implicit unit conversions.
    event ShipmentCreated(uint256 indexed shipmentId, address indexed exporter, address indexed importer, string documentCID);
    event ShipmentTransition(uint256 indexed shipmentId, State state, address indexed actor, bytes32 reviewHash);
    event QuotaConfigured(bytes32 indexed key, bytes3 country, bytes32 substance, uint16 year, bool isExport, bool national, uint256 cap, bytes32 reviewHash);
    event QuotaUsageCorrected(bytes32 indexed key, uint256 beforeUsage, uint256 afterUsage, bytes32 reviewHash);
    event QuotaReserved(uint256 indexed shipmentId, bytes32 indexed key, uint256 quantity);
    event QuotaReleased(uint256 indexed shipmentId, bytes32 indexed key, uint256 quantity);
    event QuotaConsumed(uint256 indexed shipmentId, bytes32 indexed key, uint256 quantity);
    event TransitRecorded(uint256 indexed shipmentId, string documentCID);
    event ImportPermitReplaced(uint256 indexed shipmentId, uint256 indexed previousPermitId, uint256 indexed replacementPermitId, bytes32 reviewHash);

    constructor(address reg, address comp) {
        registration = Registration(reg); compliance = Compliance(comp); INCB = registration.INCB();
        require(address(compliance.registration()) == reg, "Mismatched registration");
    }
    modifier onlyINCB() { require(msg.sender == INCB, "INCB required"); _; }
    function quotaKey(bytes3 country, bytes32 substance, uint16 year, bool isExport) public pure returns (bytes32) {
        return keccak256(abi.encode(country, substance, year, isExport));
    }
    function setQuota(bytes3 country, bytes32 substance, uint16 year, bool isExport, bool national, uint256 cap, bytes32 reviewHash) external {
        require(national ? registration.isNCA(msg.sender, country) : msg.sender == INCB, "Unauthorized quota setter");
        require(country != bytes3(0) && substance != bytes32(0) && cap > 0 && reviewHash != bytes32(0), "Invalid quota");
        bytes32 key = quotaKey(country, substance, year, isExport);
        Quota storage q = quotas[key];
        require(cap >= q.used + q.reserved, "Cap below committed usage");
        if (national) { q.nationalCap = cap; q.nationalSet = true; }
        else { require(bytes(compliance.getIncbCapIpfs(year)).length > 0, "Quota document missing"); q.globalCap = cap; q.globalSet = true; }
        emit QuotaConfigured(key, country, substance, year, isExport, national, cap, reviewHash);
    }
    function correctQuotaUsage(bytes3 country, bytes32 substance, uint16 year, bool isExport, uint256 correctedUsage, bytes32 reviewHash) external onlyINCB {
        bytes32 key = quotaKey(country, substance, year, isExport);
        Quota storage q = quotas[key];
        require(q.nationalSet && q.globalSet && reviewHash != bytes32(0), "Unconfigured quota or review");
        require(correctedUsage + q.reserved <= q.nationalCap && correctedUsage + q.reserved <= q.globalCap, "Correction exceeds cap");
        emit QuotaUsageCorrected(key, q.used, correctedUsage, reviewHash);
        q.used = correctedUsage;
    }
    function createShipment(address importer, address logistics, bytes32 substance, uint256 quantity, uint16 year, string calldata documentCID) external returns (uint256 id) {
        require(registration.isLicensed(msg.sender, Registration.Role.Exporter), "Licensed exporter required");
        require(registration.isLicensed(importer, Registration.Role.Importer) && registration.isLicensed(logistics, Registration.Role.Logistics), "Unlicensed recipient or logistics");
        require(quantity > 0 && substance != bytes32(0) && bytes(documentCID).length > 0, "Invalid consignment");
        bytes3 origin = registration.countryOf(msg.sender); bytes3 destination = registration.countryOf(importer);
        require(origin != destination, "Cross-border shipment required");
        id = nextShipmentId++;
        Shipment storage s = shipments[id];
        s.exporter = msg.sender; s.importer = importer; s.logistics = logistics;
        s.origin = origin; s.destination = destination; s.substance = substance;
        s.quantity = quantity; s.year = year; s.documentCID = documentCID; s.state = State.Created;
        emit ShipmentCreated(id, msg.sender, importer, documentCID);
    }
    function getShipment(uint256 id) public view returns (Shipment memory) {
        require(shipments[id].state != State.Missing, "Unknown shipment"); return shipments[id];
    }
    function parties(uint256 id) external view returns (address, address, bytes3, bytes3) {
        Shipment memory s = getShipment(id); return (s.exporter, s.importer, s.origin, s.destination);
    }
    function participants(uint256 id) external view returns (address, address) {
        Shipment memory s = getShipment(id); return (s.exporter, s.importer);
    }
    function jurisdictions(uint256 id) external view returns (bytes3, bytes3) {
        Shipment memory s = getShipment(id); return (s.origin, s.destination);
    }
    function linkPermit(uint256 shipmentId, uint256 permitId, bool isExport) external {
        require(msg.sender == address(compliance), "Compliance required");
        Shipment storage s = shipments[shipmentId];
        Compliance.Permit memory p = compliance.getPermit(permitId, isExport);
        require(p.shipmentId == shipmentId && p.status == Compliance.Status.Approved, "Permit mismatch");
        if (s.state != State.Created) {
            require(!isExport && s.state == State.InTransit && s.importPermit != 0, "Permit linkage locked");
            require(!compliance.isValid(s.importPermit, shipmentId, false), "Current import permit still valid");
            require(compliance.isValid(permitId, shipmentId, false), "Invalid replacement permit");
            _validImporter(s);
            uint256 previousPermit = s.importPermit;
            s.importPermit = permitId;
            emit ImportPermitReplaced(shipmentId, previousPermit, permitId, p.reviewHash);
            return;
        }
        if (isExport) {
            require(compliance.isValid(s.importPermit, shipmentId, false), "Import authorization required first");
            s.exportPermit = permitId;
        } else { s.importPermit = permitId; }
    }
    function approveExportClearance(uint256 id, bytes32 reviewHash) external {
        Shipment storage s = shipments[id];
        require(s.state == State.Created && reviewHash != bytes32(0), "Invalid state or review");
        require(registration.isLicensed(msg.sender, Registration.Role.Customs) && registration.countryOf(msg.sender) == s.origin, "Export customs required");
        _validPartiesAndPermits(id, s);
        s.state = State.ExportCleared;
        emit ShipmentTransition(id, s.state, msg.sender, reviewHash);
    }
    function approveForShipping(uint256 id, bytes32 reviewHash) external onlyINCB {
        Shipment storage s = shipments[id];
        require(s.state == State.ExportCleared && reviewHash != bytes32(0), "Invalid state or review");
        _validPartiesAndPermits(id, s);
        _reserve(id, quotaKey(s.origin, s.substance, s.year, true), s.quantity);
        _reserve(id, quotaKey(s.destination, s.substance, s.year, false), s.quantity);
        s.state = State.Approved;
        emit ShipmentTransition(id, s.state, msg.sender, reviewHash);
    }
    function pickup(uint256 id) external {
        Shipment storage s = shipments[id];
        require(s.state == State.Approved && msg.sender == s.logistics && registration.isLicensed(msg.sender, Registration.Role.Logistics), "Pickup forbidden");
        _validPartiesAndPermits(id, s);
        _consume(id, quotaKey(s.origin, s.substance, s.year, true), s.quantity);
        s.state = State.InTransit;
        emit ShipmentTransition(id, s.state, msg.sender, bytes32(0));
    }
    function recordTransit(uint256 id, string calldata documentCID) external {
        Shipment storage s = shipments[id];
        require(s.state == State.InTransit && msg.sender == s.logistics && registration.isLicensed(msg.sender, Registration.Role.Logistics), "Transit update forbidden");
        require(bytes(documentCID).length > 0, "Document required");
        emit TransitRecorded(id, documentCID);
    }
    function approveImportClearance(uint256 id, bytes32 reviewHash) external {
        Shipment storage s = shipments[id];
        require(s.state == State.InTransit && reviewHash != bytes32(0), "Invalid state or review");
        require(registration.isLicensed(msg.sender, Registration.Role.Customs) && registration.countryOf(msg.sender) == s.destination, "Import customs required");
        _validImporter(s);
        require(compliance.isValid(s.importPermit, id, false), "Invalid import permit");
        _consume(id, quotaKey(s.destination, s.substance, s.year, false), s.quantity);
        s.state = State.ImportCleared;
        emit ShipmentTransition(id, s.state, msg.sender, reviewHash);
    }
    function receiveShipment(uint256 id) external {
        Shipment storage s = shipments[id];
        require(s.state == State.ImportCleared && msg.sender == s.importer && registration.isLicensed(msg.sender, Registration.Role.Importer), "Receipt forbidden");
        _validImporter(s);
        s.state = State.Received;
        emit ShipmentTransition(id, s.state, msg.sender, bytes32(0));
    }
    function cancelShipment(uint256 id, bytes32 reviewHash) external onlyINCB {
        Shipment storage s = shipments[id];
        require(s.state >= State.Created && s.state <= State.Approved && reviewHash != bytes32(0), "Cancellation forbidden");
        if (s.state == State.Approved) {
            _release(id, quotaKey(s.origin, s.substance, s.year, true), s.quantity);
            _release(id, quotaKey(s.destination, s.substance, s.year, false), s.quantity);
        }
        s.state = State.Cancelled;
        emit ShipmentTransition(id, s.state, msg.sender, reviewHash);
    }
    function _validImporter(Shipment storage s) internal view {
        require(registration.isLicensed(s.importer, Registration.Role.Importer) && registration.countryOf(s.importer) == s.destination, "Licensed destination importer required");
    }
    function _validPartiesAndPermits(uint256 id, Shipment storage s) internal view {
        require(registration.isLicensed(s.exporter, Registration.Role.Exporter) && registration.isLicensed(s.importer, Registration.Role.Importer), "Unlicensed party");
        require(registration.countryOf(s.exporter) == s.origin && registration.countryOf(s.importer) == s.destination, "Party jurisdiction changed");
        require(compliance.isValid(s.exportPermit, id, true) && compliance.isValid(s.importPermit, id, false), "Invalid permits");
    }
    function _reserve(uint256 id, bytes32 key, uint256 quantity) internal {
        Quota storage q = quotas[key];
        require(q.nationalSet && q.globalSet && q.used + q.reserved + quantity <= q.nationalCap && q.used + q.reserved + quantity <= q.globalCap, "Insufficient quota");
        q.reserved += quantity; emit QuotaReserved(id, key, quantity);
    }
    function _consume(uint256 id, bytes32 key, uint256 quantity) internal {
        Quota storage q = quotas[key];
        require(q.reserved >= quantity, "Missing reservation");
        q.reserved -= quantity; q.used += quantity; emit QuotaConsumed(id, key, quantity);
    }
    function _release(uint256 id, bytes32 key, uint256 quantity) internal {
        quotas[key].reserved -= quantity; emit QuotaReleased(id, key, quantity);
    }
}
