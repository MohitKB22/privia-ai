# Deployment

PRIVIA is desktop software. It is designed to run on the machine whose files it
reads, and there is no multi-tenant mode: one instance serves one person.

## Native (recommended)

```bash
./scripts/setup.sh
make dev
```

## Docker

The container is for people who would rather not install Python. It does not
change the security model.

```bash
export PRIVIA_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export PRIVIA_WORKSPACE="$HOME/Documents"
export ALLOWED_DIRECTORIES=/workspace
docker compose up -d
./scripts/health_check.sh
```

Notes that matter:

* **A token is mandatory.** Inside the container the API binds to `0.0.0.0`,
  because the container's loopback is not yours. The compose file publishes the
  port on `127.0.0.1` only, and start-up refuses to run without a token.
* **Mount folders explicitly.** A container cannot see your filesystem. Anything
  PRIVIA should read must be mounted *and* listed in `ALLOWED_DIRECTORIES`.
* **The model runs on the host.** `host.docker.internal` points the container at
  an Ollama instance on your machine, so the model is not duplicated in the image.
* **Terminal tools are less useful in a container**: they execute inside it, not
  on your machine, which is usually not what you want.

## Reverse proxies and remote access

Don't. PRIVIA executes commands and reads files on behalf of whoever can reach
it. If you genuinely need remote access, use an SSH tunnel:

```bash
ssh -N -L 8756:127.0.0.1:8756 you@your-machine
```

That keeps the trust boundary where it belongs.
