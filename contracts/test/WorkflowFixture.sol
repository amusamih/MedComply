// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/Shipping.sol";

interface Vm { function prank(address) external; function expectRevert() external; function warp(uint256) external; struct Log { bytes32[] topics; bytes data; address emitter; } function recordLogs() external; function getRecordedLogs() external returns (Log[] memory); }
abstract contract WorkflowFixture {
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
}
