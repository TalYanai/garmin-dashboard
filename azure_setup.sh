#!/bin/bash
# Azure VM Setup for Garmin Dashboard Bot
# Run this from your terminal after: az login

DNS_NAME="garmin-dashboard-bot"   # שנה אם השם תפוס

# 1. Resource Group
az group create \
  --name GarminDashboard-RG \
  --location westeurope

# 2. Ubuntu 22.04 VM
az vm create \
  --resource-group GarminDashboard-RG \
  --name Garmin-VM \
  --image Ubuntu2204 \
  --size Standard_B2s \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard \
  --public-ip-address-dns-name "$DNS_NAME"

# 3. פתיחת פורטים
az vm open-port --resource-group GarminDashboard-RG --name Garmin-VM --port 80  --priority 1001
az vm open-port --resource-group GarminDashboard-RG --name Garmin-VM --port 443 --priority 1002

# הצגת כתובת ה-DNS
echo ""
echo "✅ VM מוכן!"
echo "🌐 כתובת DNS: ${DNS_NAME}.westeurope.cloudapp.azure.com"
echo "🔑 התחברות: ssh azureuser@${DNS_NAME}.westeurope.cloudapp.azure.com"
