// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import {WorkflowFixture} from "./WorkflowFixture.sol";
import {Shipping} from "../src/Shipping.sol";

contract ShipmentHandler is WorkflowFixture {
    uint256[] public ids;
    uint256 public completedActions;
    uint256 public rejectedApprovals;

    constructor() {
        setUp();
        create(0);
        approve(0);
    }
    function create(uint256 input) public {
        if (ids.length >= 16) return;
        uint256 quantity = 1 + input % 2_000_000_000;
        vm.prank(exporter); uint256 id = ship.createShipment(importer,logistics,SUBSTANCE,quantity,2025,"ipfs://shipment");
        _permits(id); vm.prank(customsUSA); ship.approveExportClearance(id,REVIEW);
        ids.push(id); completedActions++;
    }
    function approve(uint256 input) public {
        uint256 id = ids[input % ids.length];
        Shipping.Shipment memory s = ship.getShipment(id);
        if (s.state != Shipping.State.ExportCleared) return;
        (uint256 exUsed,uint256 exReserved) = _values("USA",true);
        (uint256 imUsed,uint256 imReserved) = _values("GBR",false);
        bool eligible = exUsed + exReserved + s.quantity <= 8_000_000_000 && imUsed + imReserved + s.quantity <= 8_000_000_000;
        try ship.approveForShipping(id,REVIEW) {
            require(eligible,"Approval exceeded allowance"); completedActions++;
        } catch {
            require(!eligible,"Eligible approval unexpectedly failed"); rejectedApprovals++;
        }
    }
    function pickup(uint256 input) public {
        uint256 id = ids[input % ids.length];
        if (ship.getShipment(id).state != Shipping.State.Approved) return;
        vm.prank(logistics); ship.pickup(id); completedActions++;
    }
    function importClearance(uint256 input) public {
        uint256 id = ids[input % ids.length];
        if (ship.getShipment(id).state != Shipping.State.InTransit) return;
        vm.prank(customsGBR); ship.approveImportClearance(id,REVIEW); completedActions++;
    }
    function receiveShipment(uint256 input) public {
        uint256 id = ids[input % ids.length];
        if (ship.getShipment(id).state != Shipping.State.ImportCleared) return;
        vm.prank(importer); ship.receiveShipment(id); completedActions++;
    }
    function cancel(uint256 input) public {
        uint256 id = ids[input % ids.length];
        Shipping.State state = ship.getShipment(id).state;
        if (state < Shipping.State.Created || state > Shipping.State.Approved) return;
        ship.cancelShipment(id,REVIEW); completedActions++;
    }
    function assertAccounting() external view {
        uint256 expectedExUsed; uint256 expectedExReserved;
        uint256 expectedImUsed; uint256 expectedImReserved;
        for (uint256 i; i < ids.length; i++) {
            Shipping.Shipment memory s = ship.getShipment(ids[i]);
            if (s.state == Shipping.State.Approved) { expectedExReserved += s.quantity; expectedImReserved += s.quantity; }
            if (s.state == Shipping.State.InTransit) { expectedExUsed += s.quantity; expectedImReserved += s.quantity; }
            if (s.state == Shipping.State.ImportCleared || s.state == Shipping.State.Received) {
                expectedExUsed += s.quantity; expectedImUsed += s.quantity;
            }
        }
        (uint256 exUsed,uint256 exReserved) = _values("USA",true);
        (uint256 imUsed,uint256 imReserved) = _values("GBR",false);
        require(exUsed == expectedExUsed && exReserved == expectedExReserved,"Export ledger differs from shipment states");
        require(imUsed == expectedImUsed && imReserved == expectedImReserved,"Import ledger differs from shipment states");
        require(exUsed + exReserved <= 8_000_000_000 && imUsed + imReserved <= 8_000_000_000,"Committed quantity exceeds cap");
    }
}

contract QuotaInvariantTest {
    struct FuzzSelector { address addr; bytes4[] selectors; }
    struct FuzzArtifactSelector { string artifact; bytes4[] selectors; }
    struct FuzzInterface { address addr; string[] artifacts; }
    ShipmentHandler public handler;
    function setUp() public { handler = new ShipmentHandler(); }
    function targetContracts() public view returns (address[] memory targets) {
        targets = new address[](1); targets[0] = address(handler);
    }
    function targetSelectors() public view returns (FuzzSelector[] memory targets) {
        targets = new FuzzSelector[](1);
        bytes4[] memory selectors = new bytes4[](6);
        selectors[0] = handler.create.selector; selectors[1] = handler.approve.selector;
        selectors[2] = handler.pickup.selector; selectors[3] = handler.importClearance.selector;
        selectors[4] = handler.receiveShipment.selector; selectors[5] = handler.cancel.selector;
        targets[0] = FuzzSelector(address(handler),selectors);
    }
    function targetArtifacts() public pure returns (string[] memory) { return new string[](0); }
    function excludeArtifacts() public pure returns (string[] memory) { return new string[](0); }
    function targetArtifactSelectors() public pure returns (FuzzArtifactSelector[] memory) { return new FuzzArtifactSelector[](0); }
    function targetInterfaces() public pure returns (FuzzInterface[] memory) { return new FuzzInterface[](0); }
    function excludeContracts() public pure returns (address[] memory) { return new address[](0); }
    function excludeSelectors() public pure returns (FuzzSelector[] memory) { return new FuzzSelector[](0); }
    function targetSenders() public pure returns (address[] memory) { return new address[](0); }
    function excludeSenders() public pure returns (address[] memory) { return new address[](0); }
    function invariantReservationsAndUsageMatchShipmentStates() public view {
        handler.assertAccounting();
    }
}
