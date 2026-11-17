// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// Participant permissions for the research prototype's federated governance.
contract Registration {
    enum Role { None, Customs, Exporter, Logistics, Importer }
    struct Authority { bytes3 country; bool active; }
    struct Actor { Role role; bytes3 country; string documentCID; uint64 expiry; bool active; bool pending; }
    address public immutable INCB;
    mapping(address => Authority) public authorities;
    mapping(address => Actor) private actors;
    event AuthorityChanged(address indexed account, bytes3 country, bool active);
    event RegistrationRequested(address indexed account, bytes3 country, Role role, string documentCID);
    event ActorChanged(address indexed account, bool active, uint64 expiry, bytes32 reviewHash);

    constructor() { INCB = msg.sender; }
    modifier onlyINCB() { require(msg.sender == INCB, "INCB required"); _; }
    function registerNCA(address account, bytes3 country) external onlyINCB {
        require(account != address(0) && country != bytes3(0), "Invalid authority");
        require(!authorities[account].active, "Already active");
        authorities[account] = Authority(country, true);
        emit AuthorityChanged(account, country, true);
    }
    function revokeNCA(address account) external onlyINCB {
        require(authorities[account].active, "Inactive authority");
        authorities[account].active = false;
        emit AuthorityChanged(account, authorities[account].country, false);
    }
    function requestReg(bytes3 country, Role role, string calldata documentCID) external {
        require(country != bytes3(0) && role != Role.None && bytes(documentCID).length > 0, "Invalid request");
        require(!actors[msg.sender].active && !actors[msg.sender].pending, "Existing registration");
        actors[msg.sender] = Actor(role, country, documentCID, 0, false, true);
        emit RegistrationRequested(msg.sender, country, role, documentCID);
    }
    function approveReg(address account, uint64 expiry, bytes32 reviewHash) external {
        Actor storage a = actors[account];
        require(isNCA(msg.sender, a.country) && a.pending, "NCA or pending request required");
        require(expiry > block.timestamp && reviewHash != bytes32(0), "Invalid review");
        a.expiry = expiry; a.active = true; a.pending = false;
        emit ActorChanged(account, true, expiry, reviewHash);
    }
    function updateLicense(address account, uint64 expiry, bytes32 reviewHash) external {
        Actor storage a = actors[account];
        require(isNCA(msg.sender, a.country) && a.active, "NCA or active actor required");
        require(expiry > block.timestamp && reviewHash != bytes32(0), "Invalid review");
        a.expiry = expiry;
        emit ActorChanged(account, true, expiry, reviewHash);
    }
    function revokeReg(address account, bytes32 reviewHash) external {
        Actor storage a = actors[account];
        require(isNCA(msg.sender, a.country) && a.active && reviewHash != bytes32(0), "Unauthorized revocation");
        a.active = false;
        emit ActorChanged(account, false, a.expiry, reviewHash);
    }
    function isNCA(address account, bytes3 country) public view returns (bool) {
        return authorities[account].active && authorities[account].country == country;
    }
    function isLicensed(address account, Role role) public view returns (bool) {
        Actor storage a = actors[account];
        return a.active && a.expiry >= block.timestamp && a.role == role;
    }
    function countryOf(address account) external view returns (bytes3) { return actors[account].country; }
    function getActor(address account) external view returns (Actor memory) { return actors[account]; }
}
