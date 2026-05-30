#!/bin/bash

# Install dependencies
apt-get update
apt-get install -y ca-certificates curl gnupg

# Add Pi Node repo
mkdir -p /etc/apt/keyrings
curl -fsSL https://minepi.com/pi-blockchain/pi-node/linux/pi-node.gpg | gpg --dearmor -o /etc/apt/keyrings/pi-node.gpg
echo "deb [signed-by=/etc/apt/keyrings/pi-node.gpg] https://minepi.com/pi-blockchain/pi-node/linux stable main" | tee /etc/apt/sources.list.d/pi-node.list > /dev/null

# Install pi-node
apt-get update
apt-get install -y pi-node
