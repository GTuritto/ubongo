# The outer envelope — install and verify (Linux / Raspberry Pi only)

Phase 01 of [Plans/v0.5-trust-protocol.md](../../Plans/v0.5-trust-protocol.md), recorded in
[ADR-0017](../../docs/adr/0017-deployment-envelope-podman-nftables.md). This envelope is
**Linux-only by design**; the macOS dev machine keeps the plain LAN posture (Amendment 1).

What you get: Ubongo's services run rootless under a dedicated `ubongo` user inside Podman,
`.env` is mounted read-only, and an nftables rule set rejects every outbound connection from
that user except loopback, DNS, and HTTPS to the hosts listed in `/etc/ubongo/egress.hosts`
(default: `openrouter.ai`). What leaves the machine becomes enumerable and enforced below
the model's discretion.

Prerequisites: a Pi/Ubuntu/Debian host with systemd, `podman` ≥ 4.4 (Quadlet support) and
`nftables` installed (`sudo apt install podman nftables`).

## 1. The dedicated user

```bash
sudo useradd --create-home --shell /bin/bash ubongo
sudo loginctl enable-linger ubongo          # user units survive logout/reboot
sudo -iu ubongo git clone <your-repo-url> ~/ubongo
sudo -iu ubongo cp ~/ubongo/.env.example ~/ubongo/.env   # then fill the key in
```

If you are migrating an existing install, move `data/` and `vault/` into
`/home/ubongo/ubongo/` and `chown -R ubongo:ubongo` them.

## 2. The firewall (do this BEFORE first start — fail closed from minute one)

```bash
sudo mkdir -p /etc/ubongo
sudo cp ~ubongo/ubongo/deploy/envelope/egress.hosts /etc/ubongo/egress.hosts
sudo cp ~ubongo/ubongo/deploy/envelope/refresh-egress.sh /usr/local/sbin/refresh-egress.sh
sudo chmod 755 /usr/local/sbin/refresh-egress.sh
sudo cp ~ubongo/ubongo/deploy/envelope/nftables-ubongo.conf /etc/nftables.d/ubongo.conf 2>/dev/null \
  || { sudo mkdir -p /etc/nftables.d && sudo cp ~ubongo/ubongo/deploy/envelope/nftables-ubongo.conf /etc/nftables.d/ubongo.conf; }
grep -q 'nftables.d' /etc/nftables.conf || echo 'include "/etc/nftables.d/*.conf"' | sudo tee -a /etc/nftables.conf
sudo systemctl enable --now nftables
sudo cp ~ubongo/ubongo/deploy/envelope/ubongo-egress-refresh.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ubongo-egress-refresh.timer
sudo systemctl start ubongo-egress-refresh.service     # populate the sets now
sudo nft list set inet ubongo_egress allow4            # expect openrouter.ai's addresses
```

## 3. Build the image and start the quadlets (as the ubongo user)

```bash
sudo -iu ubongo bash -lc '
  cd ~/ubongo
  podman build -t localhost/ubongo:latest -f deploy/envelope/Containerfile .
  mkdir -p ~/.config/containers/systemd
  cp deploy/envelope/ubongo-web.container deploy/envelope/ubongo-mcp.container ~/.config/containers/systemd/
  systemctl --user daemon-reload
  systemctl --user start ubongo-web ubongo-mcp
  systemctl --user status ubongo-web ubongo-mcp --no-pager
'
```

The quadlets replace `deploy/ubongo-web.service` / `deploy/ubongo-mcp.service` and
`ubongo-ctl.sh` on this host — do not run both. Web: `http://<pi>:8501`, MCP:
`http://<pi>:8765/mcp`.

## 4. Verify by attempt (the exit criterion)

```bash
# E.1 — allowlisted egress works: a real governed turn end to end
sudo -iu ubongo bash -lc 'cd ~/ubongo && podman exec ubongo-web python -m ubongo send "say hello" --persona casual'

# E.2 — non-allowlisted egress fails AT THE NETWORK LAYER
sudo -iu ubongo podman exec ubongo-web python -c \
  "import urllib.request;urllib.request.urlopen('https://example.com', timeout=5)" \
  ; echo "rc=$? (expect non-zero: connection refused/reset by nft reject)"
sudo nft list chain inet ubongo_egress output | grep counter   # reject counter advanced

# E.3 — a Connector call to an unconfigured destination dies at the network layer:
# enable a scratch server in settings.yaml pointing at https://example.com/mcp
# (NOT added to egress.hosts), then /mode connector_session — the tool call
# fails with a connection error, not a model-level refusal; /audit mcp shows it.

# E.4 — .env is read-only inside the container
sudo -iu ubongo podman exec ubongo-web sh -c 'touch /app/.env' \
  ; echo "rc=$? (expect non-zero: Read-only file system)"

# E.5 — refresh keeps old sets on resolution failure
sudo sh -c 'echo "no-such-host.invalid" >> /etc/ubongo/egress.hosts'
sudo systemctl start ubongo-egress-refresh.service; systemctl status ubongo-egress-refresh --no-pager | tail -2
sudo nft list set inet ubongo_egress allow4    # openrouter addresses still present
sudo sed -i '/no-such-host.invalid/d' /etc/ubongo/egress.hosts
```

All five pass → the envelope holds; record the run in the smoke playbook section.

## Enabling an MCP server later

Two edits, same change, both visible: the server block in `settings.yaml::mcp.servers`
**and** its hostname in `/etc/ubongo/egress.hosts` (then
`sudo systemctl start ubongo-egress-refresh.service`). Forgetting the second edit fails
closed: the Connector reports a connection error and the reject counter shows why.

## The terrarium variant

The growth experiment from the v0.5 plan is this same envelope with looser walls — run it
as a SECOND user (`ubongo-terrarium`) so the production allowlist is untouched:

- duplicate the nft table keyed on the new UID with a broader `egress.hosts`
  (or none — accept all 443 for that UID only);
- a separate clone with **inert keys** in `.env` where you want zero real spend;
- daemons resumed: in the container, `/evolution resume` and `/authoring resume`
  (they boot paused; the human approval boundary — ADRs 0006/0013 — still holds, so the
  terrarium can draft and propose but never promote itself).

## Removal

```bash
sudo -iu ubongo systemctl --user stop ubongo-web ubongo-mcp
sudo -iu ubongo rm ~/.config/containers/systemd/ubongo-{web,mcp}.container
sudo systemctl disable --now ubongo-egress-refresh.timer
sudo rm /etc/nftables.d/ubongo.conf /usr/local/sbin/refresh-egress.sh && sudo systemctl restart nftables
```
