#!/bin/bash
# scripts/setup_network.sh
# Fix intermittent VPN/network issues by clamping TCP MSS

echo "🔧 Applying TCP MSS Clamping to 1300..."
# Remove old rule to avoid duplicates
iptables -t mangle -D POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1300 2>/dev/null || true

# Add new rule
iptables -t mangle -A POSTROUTING -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1300

echo "✅ Network optimized for VPN tunnels."
