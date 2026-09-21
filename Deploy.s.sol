// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";
import "../src/RWAToken.sol";
import "../src/KYCRegistry.sol";
import "../src/MockUSDC.sol";
import "../src/OrderBook.sol";

contract DeployScript is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOY_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        console.log("=== Deploy na Polygon Amoy ===");
        console.log("Deployer:", deployer);
        console.log("Chain ID:", block.chainid);
        console.log("");

        require(block.chainid == 80002, "Este script so roda na Polygon Amoy");

        vm.startBroadcast(deployerKey);

        // 1. MockUSDC (testnet)
        MockUSDC usdc = new MockUSDC();
        console.log("MockUSDC        :", address(usdc));

        // 2. KYCRegistry
        KYCRegistry kyc = new KYCRegistry(deployer);
        console.log("KYCRegistry     :", address(kyc));

        // 3. RWAToken (BRN)
        RWAToken brn = new RWAToken(
            "BRN Token",
            "BRN",
            "currency",
            "BRN-001",
            "ipfs://QmBrnToken",
            100_000_00,
            deployer
        );
        console.log("RWAToken (BRN)  :", address(brn));

        // 4. OrderBook
        OrderBook orderBook = new OrderBook(
            address(brn),
            address(usdc),
            address(kyc),
            deployer
        );
        console.log("OrderBook       :", address(orderBook));

        // 5. Whitelist do OrderBook no RWAToken (ESSENCIAL!)
        brn.setWhitelist(address(orderBook), true);
        console.log("OrderBook whitelisted no RWAToken");

        vm.stopBroadcast();

        console.log("");
        console.log("=== Deploy concluido ===");
        console.log("");
        console.log("Salve estes enderecos:");
        console.log("USDC_ADDRESS=", address(usdc));
        console.log("KYC_REGISTRY_ADDRESS=", address(kyc));
        console.log("RWA_TOKEN_ADDRESS=", address(brn));
        console.log("ORDER_BOOK_ADDRESS=", address(orderBook));
    }
}
