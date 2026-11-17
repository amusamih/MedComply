// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {WorkflowFixture, Vm} from "./WorkflowFixture.sol";
import {Shipping} from "../src/Shipping.sol";
import {Compliance} from "../src/Compliance.sol";
import {Registration} from "../src/Registration.sol";

contract TransitRecoveryTest is WorkflowFixture {
    bytes32 constant REPLACEMENT_REVIEW = keccak256("destination-authority-replacement-review");

    function _inTransit() internal returns (uint256 id) {
        id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
    }
    function _request(uint256 id, bool ex) internal returns (uint256 pid) {
        vm.prank(ex ? exporter : importer);
        pid = comp.requestPermit(id, ex, "ipfs://reviewed-replacement");
    }
    function _approve(uint256 pid) internal {
        vm.prank(ncaGBR);
        comp.approvePermit(pid, false, uint64(block.timestamp + 30 days), REPLACEMENT_REVIEW);
    }
    function _revokeImport(uint256 id) internal {
        uint256 pid = ship.getShipment(id).importPermit;
        vm.prank(ncaGBR); comp.revokePermit(pid, false, REVIEW);
    }
    function _assertTransitAccounting(uint256 quantity) internal view {
        (uint256 used, uint256 reserved) = _values("USA", true);
        require(used == quantity && reserved == 0, "Export settlement changed");
        (used, reserved) = _values("GBR", false);
        require(used == 0 && reserved == quantity, "Import reservation changed");
    }
    function _finish(uint256 id, uint256 quantity) internal {
        vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(importer); ship.receiveShipment(id);
        (uint256 used, uint256 reserved) = _values("GBR", false);
        require(used == quantity && reserved == 0, "Import did not settle exactly once");
        require(ship.getShipment(id).state == Shipping.State.Received, "Receipt missing");
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.expectRevert(); vm.prank(importer); ship.receiveShipment(id);
    }
    function testReplacementPreservesShipmentTermsAndRecordsReviewEvent() public {
        uint256 id = _inTransit();
        Shipping.Shipment memory beforeShipment = ship.getShipment(id);
        _revokeImport(id);
        uint256 pid = _request(id, false);
        vm.recordLogs(); _approve(pid);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bool found;
        for (uint256 i; i < logs.length; ++i) {
            if (logs[i].emitter == address(ship) && logs[i].topics[0] == keccak256("ImportPermitReplaced(uint256,uint256,uint256,bytes32)")) {
                require(uint256(logs[i].topics[1]) == id && uint256(logs[i].topics[2]) == beforeShipment.importPermit && uint256(logs[i].topics[3]) == pid, "Replacement event links");
                require(abi.decode(logs[i].data, (bytes32)) == REPLACEMENT_REVIEW, "Replacement review lost"); found = true;
            }
        }
        require(found, "Replacement event missing");
        require(comp.getPermit(beforeShipment.importPermit, false).status == Compliance.Status.Revoked, "Historical permit changed");
        beforeShipment.importPermit = pid;
        require(keccak256(abi.encode(ship.getShipment(id))) == keccak256(abi.encode(beforeShipment)), "Shipment terms changed");
        _assertTransitAccounting(QTY); _finish(id, QTY);
    }
    function testValidImportPermitCannotBeOverwrittenAndApprovalRollsBack() public {
        uint256 id = _inTransit(); uint256 old = ship.getShipment(id).importPermit;
        uint256 pid = _request(id, false);
        vm.expectRevert(); _approve(pid);
        require(comp.getPermit(pid, false).status == Compliance.Status.Pending, "Partial approval");
        require(ship.getShipment(id).importPermit == old, "Valid permit overwritten");
        _assertTransitAccounting(QTY);
    }
    function testReplacementRequiresDestinationAuthorityAndHumanReview() public {
        uint256 id = _inTransit(); _revokeImport(id); uint256 pid = _request(id, false);
        vm.expectRevert(); vm.prank(ncaUSA); comp.approvePermit(pid, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.expectRevert(); vm.prank(importer); comp.approvePermit(pid, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.expectRevert(); vm.prank(ncaGBR); comp.approvePermit(pid, false, uint64(block.timestamp + 30 days), bytes32(0));
        vm.expectRevert(); ship.linkPermit(id, pid, false);
        _assertTransitAccounting(QTY);
    }
    function testReplacementRequestRequiresRecordedLicensedImporter() public {
        uint256 id = _inTransit(); _revokeImport(id);
        vm.expectRevert(); vm.prank(exporter); comp.requestPermit(id, false, "ipfs://replacement");
        vm.prank(ncaGBR); reg.revokeReg(importer, REVIEW);
        vm.expectRevert(); _request(id, false);
        _assertTransitAccounting(QTY);
    }
    function testReplacementCannotLinkAnotherShipmentsPermit() public {
        uint256 id = _inTransit(); _revokeImport(id);
        uint256 other = _create(); uint256 pid = _request(other, false);
        vm.prank(ncaGBR); comp.approvePermit(pid, false, uint64(block.timestamp + 30 days), REVIEW);
        vm.expectRevert(); vm.prank(address(comp)); ship.linkPermit(id, pid, false);
        _assertTransitAccounting(QTY);
    }
    function testExportPermitCannotBeReplacedAfterPickup() public {
        uint256 id = _inTransit(); uint256 old = ship.getShipment(id).exportPermit;
        vm.prank(ncaUSA); comp.revokePermit(old, true, REVIEW);
        uint256 pid = _request(id, true);
        vm.expectRevert(); vm.prank(ncaUSA); comp.approvePermit(pid, true, uint64(block.timestamp + 30 days), REVIEW);
        require(ship.getShipment(id).exportPermit == old, "Consumed export changed");
        _assertTransitAccounting(QTY);
    }
    function testReplacementExpiryMustBeFutureAndEqualityDoesNotInvalidateOldPermit() public {
        uint256 id = _inTransit(); uint256 old = ship.getShipment(id).importPermit;
        vm.warp(comp.getPermit(old, false).expiry);
        uint256 pid = _request(id, false);
        vm.expectRevert(); _approve(pid);
        vm.warp(block.timestamp + 1);
        vm.expectRevert(); vm.prank(ncaGBR); comp.approvePermit(pid, false, uint64(block.timestamp), REVIEW);
        _approve(pid); _assertTransitAccounting(QTY); _finish(id, QTY);
    }
    function testReplacementIsLockedAfterImportClearanceAndReceipt() public {
        uint256 id = _inTransit(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        _revokeImport(id); uint256 pid = _request(id, false);
        vm.expectRevert(); _approve(pid);
        vm.prank(importer); ship.receiveShipment(id);
        vm.expectRevert(); _approve(pid);
    }
    function testReplacementIsLockedBeforePickupAndAfterCancellation() public {
        uint256 id = _cleared(); uint256 old = ship.getShipment(id).importPermit;
        vm.prank(ncaGBR); comp.revokePermit(old, false, REVIEW);
        uint256 pid = _request(id, false);
        vm.expectRevert(); _approve(pid);
        ship.cancelShipment(id, REVIEW);
        vm.expectRevert(); _approve(pid);
        uint256 second = _cleared(); ship.approveForShipping(second, REVIEW);
        _revokeImport(second); pid = _request(second, false);
        vm.expectRevert(); _approve(pid);
        ship.cancelShipment(second, REVIEW);
    }
    function testRepeatedRevocationsAllowReviewedReplacementsWithoutNewReservations() public {
        uint256 id = _inTransit();
        for (uint256 i; i < 3; ++i) {
            _revokeImport(id); _approve(_request(id, false)); _assertTransitAccounting(QTY);
        }
        vm.expectRevert(); ship.cancelShipment(id, REVIEW);
        _finish(id, QTY);
    }
    function testImporterReinstatementInAnotherCountryCannotClearOrReplace() public {
        uint256 id = _inTransit(); _revokeImport(id); uint256 pid = _request(id, false);
        vm.prank(ncaGBR); reg.revokeReg(importer, REVIEW);
        _actor(importer, "USA", Registration.Role.Importer, ncaUSA);
        vm.expectRevert(); _approve(pid);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        _assertTransitAccounting(QTY);
    }
    function testRevocationBetweenReplacementRequestAndApprovalBlocksApproval() public {
        uint256 id = _inTransit(); _revokeImport(id); uint256 pid = _request(id, false);
        vm.prank(ncaGBR); reg.revokeReg(importer, REVIEW);
        vm.expectRevert(); _approve(pid);
        require(comp.getPermit(pid, false).status == Compliance.Status.Pending, "Approval leaked");
        _actor(importer, "GBR", Registration.Role.Importer, ncaGBR);
        _approve(pid); _finish(id, QTY);
    }
    function testImporterLicenseExpiryBlocksClearanceUntilReviewedRenewal() public {
        uint256 id = _inTransit();
        vm.prank(ncaGBR); reg.updateLicense(importer, uint64(block.timestamp + 1 days), REVIEW);
        vm.warp(block.timestamp + 2 days);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        _assertTransitAccounting(QTY);
        vm.prank(ncaGBR); reg.updateLicense(importer, uint64(block.timestamp + 30 days), REVIEW);
        _finish(id, QTY);
    }
    function testImporterReinstatementAfterClearanceCannotChangeDestination() public {
        uint256 id = _inTransit(); vm.prank(customsGBR); ship.approveImportClearance(id, REVIEW);
        vm.prank(ncaGBR); reg.revokeReg(importer, REVIEW);
        _actor(importer, "USA", Registration.Role.Importer, ncaUSA);
        vm.expectRevert(); vm.prank(importer); ship.receiveShipment(id);
        vm.prank(ncaUSA); reg.revokeReg(importer, REVIEW);
        _actor(importer, "GBR", Registration.Role.Importer, ncaGBR);
        vm.prank(importer); ship.receiveShipment(id);
        require(ship.getShipment(id).state == Shipping.State.Received, "Destination reinstatement failed");
    }
    function testFuzzReviewedReplacementPreservesQuotaAndSettlesOnce(uint64 input, bool expired) public {
        uint256 quantity = 1 + uint256(input) % 8_000_000_000;
        vm.prank(exporter); uint256 id = ship.createShipment(importer, logistics, SUBSTANCE, quantity, 2025, "ipfs://shipment");
        _permits(id); vm.prank(customsUSA); ship.approveExportClearance(id, REVIEW);
        ship.approveForShipping(id, REVIEW); vm.prank(logistics); ship.pickup(id);
        if (expired) vm.warp(block.timestamp + 31 days); else _revokeImport(id);
        _approve(_request(id, false)); _assertTransitAccounting(quantity); _finish(id, quantity);
    }
}
