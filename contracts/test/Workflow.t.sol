// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/Shipping.sol";

interface Vm { function prank(address) external; function expectRevert() external; function warp(uint256) external; }
contract WorkflowTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    Registration reg; Compliance comp; Shipping ship;
    address ncaUSA = address(11); address ncaGBR = address(12);
    address exporter = address(21); address importer = address(22); address logistics = address(23);
    address customsUSA = address(24); address customsGBR = address(25);
    bytes32 constant REVIEW = keccak256("human-reviewed-test-evidence");
    bytes32 constant SUBSTANCE = bytes32("morphine");
    uint256 constant QTY = 5_000_000_000;

    function setUp() public {
        vm.warp(1_735_689_600);
        reg = new Registration(); comp = new Compliance(address(reg));
        ship = new Shipping(address(reg), address(comp)); comp.connectShipping(address(ship));
        reg.registerNCA(ncaUSA, "USA"); reg.registerNCA(ncaGBR, "GBR");
        _actor(exporter, "USA", Registration.Role.Exporter, ncaUSA);
        _actor(importer, "GBR", Registration.Role.Importer, ncaGBR);
        _actor(logistics, "USA", Registration.Role.Logistics, ncaUSA);
        _actor(customsUSA, "USA", Registration.Role.Customs, ncaUSA);
        _actor(customsGBR, "GBR", Registration.Role.Customs, ncaGBR);
        comp.setIncbQuotas(2025, "ipfs://quota-2025", REVIEW);
        _quota("USA", true, 8_000_000_000, ncaUSA);
        _quota("GBR", false, 8_000_000_000, ncaGBR);
    }
    function _actor(address a, bytes3 country, Registration.Role role, address nca) internal {
        vm.prank(a); reg.requestReg(country, role, "ipfs://license");
        vm.prank(nca); reg.approveReg(a, uint64(block.timestamp + 365 days), REVIEW);
    }
    function _quota(bytes3 country, bool isExport, uint256 cap, address nca) internal {
        ship.setQuota(country, SUBSTANCE, 2025, isExport, false, cap, REVIEW);
        vm.prank(nca); ship.setQuota(country, SUBSTANCE, 2025, isExport, true, cap, REVIEW);
    }
    function _create() internal returns (uint256 id) {
        vm.prank(exporter); id = ship.createShipment(importer, logistics, SUBSTANCE, QTY, 2025, "ipfs://shipment");
    }
    function _permits(uint256 id) internal returns (uint256 ex, uint256 im) {
        vm.prank(importer); im = comp.requestPermit(id, false, "ipfs://import");
        vm.prank(ncaGBR); comp.approvePermit(im, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.prank(exporter); ex = comp.requestPermit(id, true, "ipfs://export");
        vm.prank(ncaUSA); comp.approvePermit(ex, true, uint64(block.timestamp + 30 days), REVIEW);
    }
    function _cleared() internal returns (uint256 id) {
        id = _create(); _permits(id);
        vm.prank(customsUSA); ship.approveExportClearance(id, REVIEW);
    }
    function _values(bytes3 country, bool ex) internal view returns (uint256 used, uint256 reserved) {
        (,,used,reserved,,) = ship.quotas(ship.quotaKey(country, SUBSTANCE, 2025, ex));
    }
    function testFullWorkflowAndExactlyOnceConsumption() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        (uint256 used, uint256 reserved) = _values("USA", true);
        require(used == 0 && reserved == QTY, "Reservation missing");
        vm.prank(logistics); ship.pickup(id);
        (used, reserved) = _values("USA", true); require(used == QTY && reserved == 0, "Export settlement");
        vm.expectRevert(); vm.prank(logistics); ship.pickup(id);
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        vm.expectRevert(); vm.prank(importer); ship.receiveShipment(id);
        (used,reserved) = _values("GBR", false); require(used == QTY && reserved == 0, "Import settlement");
        require(ship.getShipment(id).state == Shipping.State.Received, "Final state");
    }
    function testShipmentIdsAndPermitLinksRemainDistinct() public {
        uint256 first = _create(); uint256 second = _create();
        (uint256 ex2, uint256 im2) = _permits(second);
        (uint256 ex1, uint256 im1) = _permits(first);
        require(first != second && ex1 != ex2 && im1 != im2, "Identifiers collide");
        require(ship.getShipment(first).exportPermit == ex1 && ship.getShipment(second).exportPermit == ex2, "Wrong shipment link");
    }
    function testCompetingApprovalsCannotOverbook() public {
        uint256 first = _cleared(); uint256 second = _cleared();
        ship.approveForShipping(first, REVIEW);
        vm.expectRevert(); ship.approveForShipping(second, REVIEW);
        (,uint256 reserved) = _values("GBR", false); require(reserved == QTY, "Atomic reservation");
        ship.cancelShipment(first, REVIEW); ship.approveForShipping(second, REVIEW);
    }
    function testReservationBothSidesRollsBackIfImportQuotaInsufficient() public {
        uint256 id = _cleared(); ship.setQuota("GBR", SUBSTANCE, 2025, false, false, QTY - 1, REVIEW);
        vm.expectRevert(); ship.approveForShipping(id, REVIEW);
        (,uint256 reserved) = _values("USA", true); require(reserved == 0, "Half reservation leaked");
    }
    function testCancellationReleasesBothSidesAndCannotRepeat() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW); ship.cancelShipment(id, REVIEW);
        (,uint256 ex) = _values("USA", true); (,uint256 im) = _values("GBR", false);
        require(ex == 0 && im == 0, "Leaked reservation");
        vm.expectRevert(); ship.cancelShipment(id, REVIEW);
    }
    function testOutOfOrderActionsAndWrongActorsFail() public {
        uint256 id = _create();
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.expectRevert(); vm.prank(importer); ship.receiveShipment(id);
        _permits(id);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveExportClearance(id, REVIEW);
        vm.expectRevert(); vm.prank(exporter); ship.approveForShipping(id, REVIEW);
        vm.expectRevert(); ship.linkPermit(id, 1, true);
    }
    function testExportAuthorizationRequiresImportFirst() public {
        uint256 id = _create(); vm.prank(exporter); uint256 ex = comp.requestPermit(id, true, "ipfs://export");
        vm.expectRevert(); vm.prank(ncaUSA); comp.approvePermit(ex, true, uint64(block.timestamp + 30 days), REVIEW);
    }
    function testQuotaReferencesAreYearSpecific() public view {
        require(keccak256(bytes(comp.getIncbCapIpfs(2025))) == keccak256("ipfs://quota-2025"), "Wrong CID");
        require(bytes(comp.getIncbCapIpfs(2026)).length == 0, "Wrong year");
    }
    function testRevocationBlocksPickupWithoutLosingReservation() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        uint256 permitId = ship.getShipment(id).exportPermit;
        vm.prank(ncaUSA); comp.revokePermit(permitId, true, REVIEW);
        vm.expectRevert(); vm.prank(logistics); ship.pickup(id);
        ship.cancelShipment(id, REVIEW);
        (,uint256 reserved) = _values("USA", true); require(reserved == 0, "Reservation stranded");
    }
    function testUsageCorrectionReplacesRatherThanIncrements() public {
        ship.correctQuotaUsage("USA", SUBSTANCE, 2025, true, 2_000_000_000, REVIEW);
        ship.correctQuotaUsage("USA", SUBSTANCE, 2025, true, 1_000_000_000, REVIEW);
        (uint256 used,) = _values("USA", true); require(used == 1_000_000_000, "Correction accumulated");
        vm.expectRevert(); vm.prank(exporter); ship.correctQuotaUsage("USA", SUBSTANCE, 2025, true, 0, REVIEW);
    }
    function testExpiredPermitsBlockShipping() public {
        uint256 id = _cleared(); vm.warp(block.timestamp + 31 days);
        vm.expectRevert(); ship.approveForShipping(id, REVIEW);
    }
    function testExpiryAfterPickupPreservesAccountingUntilReviewedReplacement() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.warp(block.timestamp + 31 days);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.expectRevert(); ship.cancelShipment(id, REVIEW);
        (uint256 exported, uint256 exportReserved) = _values("USA", true);
        (uint256 imported, uint256 importReserved) = _values("GBR", false);
        require(exported == QTY && exportReserved == 0, "Export settlement changed");
        require(imported == 0 && importReserved == QTY, "Import reservation changed");
        vm.prank(importer); uint256 replacement = comp.requestPermit(id, false, "ipfs://replacement");
        vm.prank(ncaGBR); comp.approvePermit(replacement, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        (imported, importReserved) = _values("GBR", false);
        require(imported == QTY && importReserved == 0 && ship.getShipment(id).state == Shipping.State.Received, "Recovery settlement");
    }
    function testRevokedTransitPermitCanBeReplacedThroughReviewedPath() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        uint256 importPermit = ship.getShipment(id).importPermit;
        vm.prank(ncaGBR); comp.revokePermit(importPermit, false, REVIEW);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); uint256 replacement = comp.requestPermit(id, false, "ipfs://replacement");
        vm.prank(ncaGBR); comp.approvePermit(replacement, false, uint64(block.timestamp + 30 days), REVIEW);
        require(comp.getPermit(replacement, false).status == Compliance.Status.Approved, "Replacement not approved");
        vm.expectRevert(); ship.cancelShipment(id, REVIEW);
        (uint256 used, uint256 reserved) = _values("USA", true);
        require(used == QTY && reserved == 0, "Consumed export must remain consumed");
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        require(ship.getShipment(id).state == Shipping.State.Received, "Receipt after replacement");
    }
    function testImporterRevocationAfterPickupRequiresAuthorizedReinstatement() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.prank(ncaGBR); reg.revokeReg(importer, REVIEW);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        require(ship.getShipment(id).state == Shipping.State.InTransit, "Invalid license cleared");
        vm.expectRevert(); vm.prank(importer); ship.receiveShipment(id);
        _actor(importer, "GBR", Registration.Role.Importer, ncaGBR);
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        require(ship.getShipment(id).state == Shipping.State.Received, "Receipt after reinstatement");
    }
    function testPermitExpiryEqualityRemainsValid() public {
        uint256 id = _cleared(); vm.warp(block.timestamp + 30 days);
        ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        require(ship.getShipment(id).state == Shipping.State.Received, "Expiry equality should be valid");
    }
    function testRejectedPermitCannotBeApprovedOrRejectedAgain() public {
        uint256 id = _create(); vm.prank(importer); uint256 im = comp.requestPermit(id, false, "ipfs://import");
        vm.prank(ncaGBR); comp.rejectPermit(im, false, REVIEW);
        require(comp.getPermit(im, false).status == Compliance.Status.Rejected, "Rejection not recorded");
        vm.expectRevert(); vm.prank(ncaGBR); comp.approvePermit(im, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.expectRevert(); vm.prank(ncaGBR); comp.rejectPermit(im, false, REVIEW);
    }
    function testFuzzReservationAccounting(uint64 quantity) public {
        uint256 boundedQuantity = 1 + uint256(quantity) % 8_000_000_000;
        vm.prank(exporter); uint256 id = ship.createShipment(importer, logistics, SUBSTANCE, boundedQuantity, 2025, "ipfs://shipment");
        _permits(id); vm.prank(customsUSA); ship.approveExportClearance(id, REVIEW);
        ship.approveForShipping(id, REVIEW); ship.cancelShipment(id, REVIEW);
        (uint256 used,uint256 reserved) = _values("USA", true); require(used == 0 && reserved == 0, "Fuzz accounting");
    }
}
