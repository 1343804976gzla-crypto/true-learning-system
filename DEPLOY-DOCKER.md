# Docker Deployment

This setup runs the current FastAPI app on this host and stores the live SQLite databases in a Docker named volume for better stability on Windows.

## What this deployment does

- Imports your current database files from `./data` into a Docker volume
- Starts one long-running container on this machine
- Enables `SINGLE_USER_MODE=true` so all devices share the same logical user scope
- Exposes the app on port `8000` by default

## Start

```powershell
cd C:\Users\35456\true-learning-system
.\scripts\start_docker_host.ps1
```

## Stop

```powershell
cd C:\Users\35456\true-learning-system
docker compose down
```

## Restart after code changes

```powershell
cd C:\Users\35456\true-learning-system
docker compose up -d --build
```

## Logs

```powershell
cd C:\Users\35456\true-learning-system
docker compose logs -f app
```

## Health check

Open:

```text
http://localhost:8000/health
```

You should see a JSON response with `"status": "healthy"`.

## Access from other devices

Find this host IP:

```powershell
ipconfig
```

Then open this on your other device:

```text
http://<host-ip>:8000
```

For this deployment, the default host port is `18000`, so the actual URL is usually:

```text
http://<host-ip>:18000
```

## Allow LAN access

Open an elevated PowerShell window and run:

```powershell
cd C:\Users\35456\true-learning-system
.\scripts\enable_remote_access.ps1
```

## Important notes

- This is best for LAN use or private-network use.
- Do not expose port `8000` directly to the public internet without adding authentication or a private network layer.
- If Windows Firewall blocks access from other devices, allow inbound TCP on port `8000`.
- Optional local bridge integrations such as OpenViking/OpenManus are disabled in this Docker setup unless you explicitly mount and configure them.
- The live databases now run inside the Docker volume `true-learning-system_tls_app_data`, not directly on the Windows bind mount.
