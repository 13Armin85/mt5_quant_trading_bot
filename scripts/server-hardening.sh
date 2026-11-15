#!/usr/bin/env bash
# Apply after inspecting the host and provisioning its application services.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [[ $EUID -ne 0 ]]; then
    echo 'Run as root on the deployment server.' >&2
    exit 1
fi
apt-get update
apt-get install -y --no-install-recommends fail2ban unattended-upgrades
install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-noatech-limits.conf <<'CONF'
MaxAuthTries 3
LoginGraceTime 30
MaxStartups 10:30:30
PermitEmptyPasswords no
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding local
PermitTunnel no
CONF
# Keep current password/key access intact until a second, key-based session
# has been verified. Do not lock the owner out during initial provisioning.
/usr/sbin/sshd -t
ssh_port=$(/usr/sbin/sshd -T | awk '$1 == "port" && !found {print $2; found=1}')
cat > /etc/fail2ban/jail.d/noatech.local <<CONF
[sshd]
enabled = true
backend = systemd
port = $ssh_port
maxretry = 5
findtime = 600
bantime = 3600
CONF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
cat > /etc/sysctl.d/60-noatech.conf <<'CONF'
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
CONF
sysctl --system >/dev/null
systemctl reload ssh
systemctl enable --now fail2ban unattended-upgrades
systemctl restart fail2ban
echo 'Host protections configured. Verify effective SSH settings, firewall rules and service health.'
