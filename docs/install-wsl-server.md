# Server topology on Windows (WSL2)

When running **Server (inference only)** on Windows 11 via WSL2, the host must expose SSH and the inference port to LAN clients.

## Prerequisites

- Windows 11 23H2 or later (mirrored networking)
- WSL2 with Ubuntu (`wsl --install`)
- Administrator access (PowerShell as Admin)

## 1. Enable WSL2 mirrored networking

WSL2 NAT does not expose ports to other LAN machines. Edit `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

Then from PowerShell:

```powershell
wsl --shutdown
```

After restart, ports inside WSL (SSH 22, inference 8080 or 11434) are reachable at the Windows machine’s LAN IP.

## 2. Windows firewall

PowerShell as Administrator:

```powershell
New-NetFirewallRule -DisplayName "Versa AGi SSH (WSL)" -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow
New-NetFirewallRule -DisplayName "Versa AGi Inference (WSL)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
```

Ollama on 11434 needs a matching inbound rule if that is the backend.

```powershell
Get-NetFirewallRule -DisplayName "*WSL*" | Format-Table DisplayName, Enabled, Direction, Action -Auto
```

## 3. SSH inside WSL

```bash
sudo apt update && sudo apt install -y openssh-server
sudo service ssh start
```

Optional `.bashrc` helper:

```bash
if ! pgrep -x sshd > /dev/null; then
  sudo service ssh start
fi
```

## 4. Client connection

On the **client** (agents):

```bash
WIN_IP="192.168.x.x"
ssh-copy-id -i ~/.ssh/id_ed25519.pub USER@$WIN_IP
ssh -o ConnectTimeout=5 USER@$WIN_IP echo "SSH works"
ssh -N -L 8080:localhost:8080 USER@$WIN_IP &
curl -sf --connect-timeout 5 -H "Authorization: Bearer versa-sk" http://localhost:8080/v1/models
```

> If you see **Too many authentication failures**, use `ssh -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 USER@$WIN_IP`.

> If the Windows machine previously ran Linux on the same IP: `ssh-keygen -R 192.168.x.x`.

Setup on the client still creates `versa-agi-tunnel.service` for the standing tunnel. Inference location defaults to **this machine**; choose **Remote server** only for this topology.

## Related

- [Models](models.md) — client `model refresh`
- [Backup and restore](backup-restore.md)
