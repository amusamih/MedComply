// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {WorkflowFixture, Vm} from "./WorkflowFixture.sol";
import {Registration} from "../src/Registration.sol";
import {Compliance} from "../src/Compliance.sol";
import {Shipping} from "../src/Shipping.sol";

contract RegistrationUnitTest is WorkflowFixture {
    function testRegistrationRequestStoresPendingIdentity() public {
        address applicant = address(31);
        vm.prank(applicant); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://new-license");
        Registration.Actor memory a = reg.getActor(applicant);
        require(a.country == "USA" && a.role == Registration.Role.Exporter && a.pending && !a.active && a.expiry == 0);
        require(keccak256(bytes(a.documentCID)) == keccak256("ipfs://new-license"));
    }
    function testRegistrationApprovalRequiresCountryAuthority() public {
        address applicant = address(31);
        vm.prank(applicant); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
        vm.expectRevert(); vm.prank(ncaGBR); reg.approveReg(applicant, uint64(block.timestamp + 1 days), REVIEW);
        vm.prank(ncaUSA); reg.approveReg(applicant, uint64(block.timestamp + 1 days), REVIEW);
        require(reg.isLicensed(applicant, Registration.Role.Exporter) && !reg.getActor(applicant).pending);
    }
    function testLicenseUpdateRequiresAuthorityAndStoresExpiry() public {
        uint64 expiry = uint64(block.timestamp + 400 days);
        vm.expectRevert(); vm.prank(ncaGBR); reg.updateLicense(exporter, expiry, REVIEW);
        vm.prank(ncaUSA); reg.updateLicense(exporter, expiry, REVIEW);
        require(reg.getActor(exporter).expiry == expiry);
    }
    function testRevokedAuthorityCannotApproveRegistration() public {
        reg.revokeNCA(ncaUSA);
        require(!reg.isNCA(ncaUSA, "USA"));
        vm.prank(address(31)); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
        vm.expectRevert(); vm.prank(ncaUSA); reg.approveReg(address(31), uint64(block.timestamp + 1 days), REVIEW);
    }
    function testAuthorityAdministrationRequiresIncb() public {
        vm.expectRevert(); vm.prank(exporter); reg.registerNCA(address(31), "CAN");
        vm.expectRevert(); vm.prank(exporter); reg.revokeNCA(ncaUSA);
    }
    function testExpiredAndRevokedLicensesFailRoleCheck() public {
        vm.prank(ncaUSA); reg.revokeReg(exporter, REVIEW);
        require(!reg.isLicensed(exporter, Registration.Role.Exporter));
        vm.warp(block.timestamp + 366 days);
        require(!reg.isLicensed(importer, Registration.Role.Importer));
    }
    function testDuplicateActiveAndPendingRegistrationsRevert() public {
        vm.expectRevert(); vm.prank(exporter); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
        vm.prank(address(31)); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
        vm.expectRevert(); vm.prank(address(31)); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
    }
    function testInvalidRegistrationInputsAndReviewsRevert() public {
        vm.expectRevert(); vm.prank(address(31)); reg.requestReg(bytes3(0), Registration.Role.Exporter, "ipfs://license");
        vm.expectRevert(); vm.prank(address(31)); reg.requestReg("USA", Registration.Role.None, "ipfs://license");
        vm.expectRevert(); vm.prank(address(31)); reg.requestReg("USA", Registration.Role.Exporter, "");
        vm.prank(address(31)); reg.requestReg("USA", Registration.Role.Exporter, "ipfs://license");
        vm.expectRevert(); vm.prank(ncaUSA); reg.approveReg(address(31), uint64(block.timestamp), REVIEW);
        vm.expectRevert(); vm.prank(ncaUSA); reg.approveReg(address(31), uint64(block.timestamp + 1 days), bytes32(0));
    }
}

contract PermitUnitTest is WorkflowFixture {
    function testPermitRequestStoresPartyShipmentAndDocument() public {
        uint256 shipment = _create();
        vm.prank(importer); uint256 id = comp.requestPermit(shipment, false, "ipfs://import");
        Compliance.Permit memory p = comp.getPermit(id, false);
        require(p.shipmentId == shipment && p.status == Compliance.Status.Pending && p.expiry == 0);
        require(keccak256(bytes(p.documentCID)) == keccak256("ipfs://import"));
    }
    function testWrongPartyCannotRequestPermit() public {
        uint256 shipment = _create();
        vm.expectRevert(); vm.prank(exporter); comp.requestPermit(shipment, false, "ipfs://import");
        vm.expectRevert(); vm.prank(importer); comp.requestPermit(shipment, true, "ipfs://export");
    }
    function testWrongCountryAuthorityCannotApprovePermit() public {
        uint256 shipment = _create();
        vm.prank(importer); uint256 id = comp.requestPermit(shipment, false, "ipfs://import");
        vm.expectRevert(); vm.prank(ncaUSA); comp.approvePermit(id, false, uint64(block.timestamp + 30 days), REVIEW);
        require(comp.getPermit(id, false).status == Compliance.Status.Pending);
    }
    function testApprovalStoresReviewExpiryAndShipmentLink() public {
        uint256 shipment = _create(); uint64 expiry = uint64(block.timestamp + 30 days);
        vm.prank(importer); uint256 id = comp.requestPermit(shipment, false, "ipfs://import");
        vm.prank(ncaGBR); comp.approvePermit(id, false, expiry, REVIEW);
        Compliance.Permit memory p = comp.getPermit(id, false);
        require(p.status == Compliance.Status.Approved && p.expiry == expiry && p.reviewHash == REVIEW);
        require(ship.getShipment(shipment).importPermit == id && comp.isValid(id, shipment, false));
    }
    function testPermitApprovalRejectsMissingReviewAndInvalidExpiry() public {
        uint256 shipment = _create();
        vm.prank(importer); uint256 id = comp.requestPermit(shipment, false, "ipfs://import");
        vm.expectRevert(); vm.prank(ncaGBR); comp.approvePermit(id, false, uint64(block.timestamp), REVIEW);
        vm.expectRevert(); vm.prank(ncaGBR); comp.approvePermit(id, false, uint64(block.timestamp + 30 days), bytes32(0));
        require(comp.getPermit(id, false).status == Compliance.Status.Pending);
    }
    function testQuotaDocumentPublicationRequiresIncb() public {
        vm.expectRevert(); vm.prank(exporter); comp.setIncbQuotas(2026, "ipfs://quota", REVIEW);
        comp.setIncbQuotas(2026, "ipfs://quota", REVIEW);
        require(keccak256(bytes(comp.getIncbCapIpfs(2026))) == keccak256("ipfs://quota"));
    }
    function testShippingConnectionCannotBeReplaced() public {
        vm.expectRevert(); comp.connectShipping(address(ship));
        require(address(comp.shipping()) == address(ship));
    }
    function testRevocationInvalidatesAnApprovedPermit() public {
        uint256 shipment = _create(); (uint256 ex,) = _permits(shipment);
        vm.prank(ncaUSA); comp.revokePermit(ex, true, REVIEW);
        require(comp.getPermit(ex, true).status == Compliance.Status.Revoked && !comp.isValid(ex, shipment, true));
    }
}

contract ShippingUnitTest is WorkflowFixture {
    function testCreationStoresParticipantsQuantityAndState() public {
        uint256 id = _create(); Shipping.Shipment memory s = ship.getShipment(id);
        require(s.exporter == exporter && s.importer == importer && s.logistics == logistics);
        require(s.origin == "USA" && s.destination == "GBR" && s.quantity == QTY && s.substance == SUBSTANCE);
        require(s.year == 2025 && s.state == Shipping.State.Created);
    }
    function testInvalidConsignmentInputsRevert() public {
        vm.expectRevert(); vm.prank(exporter); ship.createShipment(importer, logistics, SUBSTANCE, 0, 2025, "ipfs://shipment");
        vm.expectRevert(); vm.prank(exporter); ship.createShipment(importer, logistics, bytes32(0), QTY, 2025, "ipfs://shipment");
        vm.expectRevert(); vm.prank(exporter); ship.createShipment(importer, logistics, SUBSTANCE, QTY, 2025, "");
        _actor(address(31), "USA", Registration.Role.Importer, ncaUSA);
        vm.expectRevert(); vm.prank(exporter); ship.createShipment(address(31), logistics, SUBSTANCE, QTY, 2025, "ipfs://shipment");
        vm.expectRevert(); vm.prank(importer); ship.createShipment(importer, logistics, SUBSTANCE, QTY, 2025, "ipfs://shipment");
    }
    function testUnlicensedLogisticsBlocksCreation() public {
        vm.prank(ncaUSA); reg.revokeReg(logistics, REVIEW);
        vm.expectRevert(); _create();
    }
    function testTransitRecordRequiresAssignedLogisticsAndDocument() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.expectRevert(); vm.prank(exporter); ship.recordTransit(id, "ipfs://transit");
        vm.expectRevert(); vm.prank(logistics); ship.recordTransit(id, "");
        vm.prank(logistics); ship.recordTransit(id, "ipfs://transit");
        require(ship.getShipment(id).state == Shipping.State.InTransit);
    }
    function testClearanceAndShippingApprovalRequireReview() public {
        uint256 id = _create(); _permits(id);
        vm.expectRevert(); vm.prank(customsUSA); ship.approveExportClearance(id, bytes32(0));
        vm.prank(customsUSA); ship.approveExportClearance(id, REVIEW);
        vm.expectRevert(); ship.approveForShipping(id, bytes32(0));
        require(ship.getShipment(id).state == Shipping.State.ExportCleared);
    }
    function testCancellationAfterPickupReverts() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.expectRevert(); ship.cancelShipment(id, REVIEW);
        require(ship.getShipment(id).state == Shipping.State.InTransit);
    }
}

contract QuotaUnitTest is WorkflowFixture {
    function testQuotaConfigurationRequiresCorrectAuthority() public {
        vm.expectRevert(); vm.prank(ncaGBR); ship.setQuota("USA", SUBSTANCE, 2025, true, true, 9_000_000_000, REVIEW);
        vm.expectRevert(); vm.prank(exporter); ship.setQuota("USA", SUBSTANCE, 2025, true, false, 9_000_000_000, REVIEW);
        vm.prank(ncaUSA); ship.setQuota("USA", SUBSTANCE, 2025, true, true, 9_000_000_000, REVIEW);
        (uint256 nationalCap,,,,,) = ship.quotas(ship.quotaKey("USA", SUBSTANCE, 2025, true));
        require(nationalCap == 9_000_000_000);
    }
    function testCapCannotBeLoweredBelowReservations() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.expectRevert(); ship.setQuota("USA", SUBSTANCE, 2025, true, false, QTY - 1, REVIEW);
        vm.expectRevert(); vm.prank(ncaGBR); ship.setQuota("GBR", SUBSTANCE, 2025, false, true, QTY - 1, REVIEW);
        (,uint256 ex) = _values("USA", true); (,uint256 im) = _values("GBR", false);
        require(ex == QTY && im == QTY);
    }
    function testCapCannotBeLoweredBelowConsumedUsage() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.prank(logistics); ship.pickup(id);
        vm.expectRevert(); vm.prank(ncaUSA); ship.setQuota("USA", SUBSTANCE, 2025, true, true, QTY - 1, REVIEW);
        (uint256 used,) = _values("USA", true); require(used == QTY);
    }
    function testUsageCorrectionRespectsCapsAndReservations() public {
        uint256 id = _cleared(); ship.approveForShipping(id, REVIEW);
        vm.expectRevert(); ship.correctQuotaUsage("USA", SUBSTANCE, 2025, true, 3_000_000_001, REVIEW);
        ship.correctQuotaUsage("USA", SUBSTANCE, 2025, true, 3_000_000_000, REVIEW);
        (uint256 used,uint256 reserved) = _values("USA", true);
        require(used == 3_000_000_000 && reserved == QTY);
    }
    function testUnconfiguredQuotaCannotBeCorrected() public {
        vm.expectRevert(); ship.correctQuotaUsage("USA", bytes32("unknown"), 2025, true, 0, REVIEW);
    }
    function testExactRemainingAllowanceCanBeReserved() public {
        uint256 id = _cleared(); _quota("USA", true, QTY, ncaUSA); _quota("GBR", false, QTY, ncaGBR);
        ship.approveForShipping(id, REVIEW);
        (,uint256 ex) = _values("USA", true); (,uint256 im) = _values("GBR", false);
        require(ex == QTY && im == QTY);
    }
    function testReservationFailureLeavesBothSidesAndStateUnchanged() public {
        uint256 id = _cleared(); vm.prank(ncaGBR); ship.setQuota("GBR", SUBSTANCE, 2025, false, true, QTY - 1, REVIEW);
        vm.expectRevert(); ship.approveForShipping(id, REVIEW);
        (uint256 exUsed,uint256 exReserved) = _values("USA", true);
        (uint256 imUsed,uint256 imReserved) = _values("GBR", false);
        require(exUsed == 0 && exReserved == 0 && imUsed == 0 && imReserved == 0);
        require(ship.getShipment(id).state == Shipping.State.ExportCleared);
    }
    function testQuotaEventsMatchReservationAndConsumption() public {
        uint256 id = _cleared(); vm.recordLogs(); ship.approveForShipping(id, REVIEW);
        Vm.Log[] memory logs = vm.getRecordedLogs(); uint256 reservedEvents;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == address(ship) && logs[i].topics[0] == keccak256("QuotaReserved(uint256,bytes32,uint256)")) {
                require(uint256(logs[i].topics[1]) == id && abi.decode(logs[i].data,(uint256)) == QTY);
                bytes32 key = logs[i].topics[2];
                require(key == ship.quotaKey("USA",SUBSTANCE,2025,true) || key == ship.quotaKey("GBR",SUBSTANCE,2025,false));
                reservedEvents++;
            }
        }
        require(reservedEvents == 2);
        vm.recordLogs(); vm.prank(logistics); ship.pickup(id); logs = vm.getRecordedLogs(); uint256 consumedEvents;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == address(ship) && logs[i].topics[0] == keccak256("QuotaConsumed(uint256,bytes32,uint256)")) {
                require(uint256(logs[i].topics[1]) == id && logs[i].topics[2] == ship.quotaKey("USA",SUBSTANCE,2025,true));
                require(abi.decode(logs[i].data,(uint256)) == QTY); consumedEvents++;
            }
        }
        require(consumedEvents == 1);
    }
}

contract QuotaPropertiesTest is WorkflowFixture {
    function _approvedQuantity(uint64 input) internal returns (uint256 id,uint256 quantity) {
        quantity = 1 + uint256(input) % 8_000_000_000;
        vm.prank(exporter); id = ship.createShipment(importer,logistics,SUBSTANCE,quantity,2025,"ipfs://shipment");
        _permits(id); vm.prank(customsUSA); ship.approveExportClearance(id,REVIEW);
        ship.approveForShipping(id,REVIEW);
        (,uint256 ex) = _values("USA",true); (,uint256 im) = _values("GBR",false);
        require(ex == quantity && im == quantity);
    }
    function testFuzzCancellationReleasesBothCountryReservations(uint64 input) public {
        (uint256 id,) = _approvedQuantity(input); ship.cancelShipment(id,REVIEW);
        (uint256 exUsed,uint256 exReserved) = _values("USA",true);
        (uint256 imUsed,uint256 imReserved) = _values("GBR",false);
        require(exUsed == 0 && exReserved == 0 && imUsed == 0 && imReserved == 0);
        require(ship.getShipment(id).state == Shipping.State.Cancelled);
    }
    function testFuzzSettlementConsumesEachCountryOnce(uint64 input) public {
        (uint256 id,uint256 quantity) = _approvedQuantity(input);
        vm.prank(logistics); ship.pickup(id);
        (uint256 exUsed,uint256 exReserved) = _values("USA",true);
        (uint256 imUsed,uint256 imReserved) = _values("GBR",false);
        require(exUsed == quantity && exReserved == 0 && imUsed == 0 && imReserved == quantity);
        vm.expectRevert(); vm.prank(logistics); ship.pickup(id);
        vm.prank(customsGBR); ship.approveImportClearance(id,REVIEW);
        vm.expectRevert(); vm.prank(customsGBR); ship.approveImportClearance(id,REVIEW);
        (exUsed,exReserved) = _values("USA",true); (imUsed,imReserved) = _values("GBR",false);
        require(exUsed == quantity && exReserved == 0 && imUsed == quantity && imReserved == 0);
    }
}
