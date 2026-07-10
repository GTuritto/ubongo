# Signal channel setup (signal-cli sidecar)

The Signal channel (`ubongo signal`, v0.7) talks to a locally-run
[signal-cli](https://github.com/AsamK/signal-cli) daemon over a JSON-RPC UNIX
socket. signal-cli is the transport; Ubongo never imports libsignal and holds no
Signal secret (the credential is signal-cli's own on-disk keystore). This is a
one-time operational setup, not part of the turn path.

Ubongo registers as a **dedicated number** (its own Signal identity), not a linked
device — keeping your personal account clean and reply attribution unambiguous
(ADR-0024).

## 1. Install the prerequisite

signal-cli is a Java program; it needs a JRE (17+).

```sh
# macOS
brew install signal-cli            # pulls a JRE too

# Debian/Raspberry Pi (see the signal-cli releases page for the current tarball)
sudo apt-get install openjdk-17-jre-headless
# then unpack signal-cli-<version>.tar.gz onto PATH
```

Pin the signal-cli version you deploy with; the JSON-RPC surface can drift between
releases.

## 2. Register the dedicated number

You need a phone number that can receive an SMS or voice verification code (a
second SIM, a VoIP number, etc.). Signal now requires solving a captcha to
register.

```sh
# 1. Get a captcha token: open https://signalcaptchas.org/registration/generate.html
#    solve it, then copy the "signalcaptcha://..." token from the completed link.
signal-cli -a +<number> register --captcha <token>

# 2. Enter the code Signal sends to +<number>:
signal-cli -a +<number> verify <code>

# 3. (optional) set a profile name so recipients see who is messaging them:
signal-cli -a +<number> updateProfile --name "Ubongo"
```

## 3. Start the daemon (the JSON-RPC socket)

Run signal-cli in daemon mode exposing a JSON-RPC UNIX socket. Ubongo's client
connects to this socket, receives incoming messages, and sends replies.

```sh
signal-cli -a +<number> daemon --socket /run/ubongo/signal.sock
```

Keep the daemon running alongside Ubongo. (Supervising it with a systemd unit /
start script and packaging the `[signal]` bits is Phase 01; for now run it by
hand or under your own supervisor.)

## 4. Point Ubongo at it

In `config/settings.yaml`, under `signal:`

```yaml
signal:
  account: "+<number>"                 # the dedicated number registered above
  socket: "/run/ubongo/signal.sock"    # the daemon's JSON-RPC socket
  allowed_numbers:                     # who may drive the channel — EMPTY = deny all
    - "+<your-personal-number>"
  delivery_paused: false
```

`allowed_numbers` is fail-closed: an empty list refuses everyone, and an unlisted
sender is refused with no turn run. There is **no token in config** — the Signal
credential lives only in signal-cli's keystore.

## 5. Run the channel

```sh
ubongo signal
```

From an allowed number, message the dedicated number; the text runs as a full
governed turn through `master.handle` and the reply comes back over Signal. An
unlisted sender gets "Not authorized." and no turn runs.

If the daemon socket is missing or `signal.socket` is unset, `ubongo signal`
prints a hint and exits with code 1 (no traceback).

> **Scope (Phase 00):** a normal turn round-trips. Approve-later over Signal (the
> `/approve|/decline|/pending|/grants` command router), the `[signal]` extra, and
> the ctl/systemd ops surfaces land in Phase 01.
