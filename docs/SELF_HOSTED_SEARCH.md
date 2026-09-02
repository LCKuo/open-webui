# Interact Private Search

## Architecture

SearXNG runs as a dedicated Docker service on the Agent/CRM EC2 host. It listens only on EC2 `127.0.0.1:8888`, so no AWS inbound rule, Cloudflare hostname, or public API is required.

When local WebUI starts, an SSH process forwards:

```text
WebUI -> 127.0.0.1:8082 -> SSH -> EC2 127.0.0.1:8888 -> SearXNG
```

The Website process and databases are not mounted into the search container.

## First Installation

Run once from the project root:

```bat
install_self_hosted_search.bat
```

The installer first reads the dedicated private-search target from:

```text
.runtime/searxng-target.env
```

The local file uses this format and is intentionally ignored by Git:

```dotenv
EC2_USER_URL=ec2-user@43.212.90.118
PEM_KEY=C:\Users\KHUser\Downloads\interact-ai.pem
```

If that file is absent, the installer falls back to the Website deployment target for backward compatibility:

```text
D:\AntigravityProj\website\.deploy\ec2-target.env
```

It installs Docker when needed, installs the `interact-searxng.service` systemd unit, verifies a JSON search, opens the local tunnel, and updates the active WebUI database settings. After WebUI moves to the same EC2 host, it can use `http://127.0.0.1:8888/search` directly and the SSH tunnel is no longer required.

## Daily Use

Use the existing launchers. Both now start and validate private search before WebUI:

```bat
start_webui_tunnel.bat
start_dev_webui.bat
```

Manual controls:

```bat
start_search_service.bat
stop_search_service.bat
```

## Verification

Local tunnel:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\manage_searxng_tunnel.ps1 -Action Status
```

EC2 service:

```bash
sudo systemctl status interact-searxng.service
curl --get --data-urlencode 'q=Taiwan industrial equipment' --data 'format=json' http://127.0.0.1:8888/search
```

The service must appear only on `127.0.0.1:8888`. Do not add port 8888 to the AWS Security Group.

## Recovery

If private search must be removed from WebUI temporarily:

```bat
restore_previous_search_config.bat
```

This restores the five search settings captured before the first switch and stops the local tunnel. It does not delete the remote SearXNG service.

To reinstall or update the pinned image intentionally, update the digest in both files below, run tests, then rerun the installer:

```text
infra/searxng/interact-searxng.service
infra/searxng/install-ec2.sh
```

## Common Failures

- `Private search did not become healthy`: verify SSH access and `systemctl status interact-searxng` on EC2.
- Local port `8082` is occupied: stop the conflicting process or pass another local port and update the WebUI query URL together.
- Search returns few sources: inspect `unresponsive_engines` in SearXNG JSON; this is an upstream engine limitation, not an Open WebUI parser error.
- EC2 resource pressure: inspect `docker stats interact-searxng`; the service is capped at 512 MB RAM and 0.75 CPU.
