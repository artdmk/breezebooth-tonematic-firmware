# Changelog

Two things version independently, because they are deployed by different means:

- **Core** — `boot.py`, `code.py`, `nus.py`, `updater.py`, `recovery.py`.
  Installed over USB. Changing it means visiting every unit.
- **Payload** — `app.py`. Shipped over BLE by the booth. Changing it costs
  nothing.

Anything that can be put in the payload should be.

## Core

### 1.0.0 — 2026-08-11 — over-the-air updates

First core release. Splits the old single-file `code.py` into an immutable core
plus a replaceable payload, and adds the BLE update protocol
([docs/PROTOCOL.md](docs/PROTOCOL.md)). Verified running on hardware (unit
`6C73461C26C8A651`).

- `boot.py` gives the filesystem to the script during normal service, and to
  USB when the board is powered up off-hook — the recovery gesture.
- `code.py` runs the payload inside a try/except, restores `app_prev.py` if it
  raises or if three boots pass without the booth confirming it, and falls back
  to `recovery.py`.
- `nus.py` owns the radio and the advertised name, so a payload cannot change
  how a unit is discovered.
- `updater.py` implements the protocol: staged writes, CRC-32 verification,
  apply/confirm, rollback.
- `recovery.py` keeps a unit with a broken payload advertising and updatable.
- `tools/install.py` performs the USB install.

**Requires a USB visit.** This is the visit that makes future ones unnecessary;
fold it into the payload rollout rather than doing two.

Four constraints were found the hard way while getting this onto a board. They
are not obvious from the code, and each one is load-bearing:

- **The UART receive buffer is a fixed 64 bytes.** `buffer_size` is a parameter
  of the `StreamIn`/`StreamOut` characteristics *inside* `UARTService`, fixed
  where the class is defined — passing it to the constructor raises
  `TypeError`, and enlarging it would mean redefining the whole service. So
  `MAX_DATA` is **52**, making a whole line (`u:d ` + 52 + newline = 57 bytes)
  fit. The booth reads this from the `u:begin` reply rather than assuming it.
- **`code.py` must survive being the only part of the core present.** A
  mid-copy auto-reload runs exactly that, so every import is guarded and
  failure calls `panic()` — a slow red blink and a console message naming the
  missing file — instead of dropping to the REPL.
- **Install with `tools/install.py`, not Finder.** CircuitPython reloads after
  every file that lands, so an alphabetical copy runs `code.py` third and
  interrupts itself repeatedly. The installer holds the REPL to suspend
  auto-reload, copies in dependency order, and reads the console back to
  confirm.
- **Auto-reload stays on in normal service**, suspended only for the duration
  of a transfer (`updater.set_autoreload`). That is what makes "copy the
  missing file over USB" a working repair.

The `u:v` reply's filesystem field distinguishes `offhook` from `noboot` rather
than reporting a bare `ro`. Both mean the script cannot write firmware, but the
fixes are opposite — hang the handset up and power-cycle, versus finish the USB
install — and an operator told the wrong one goes round in circles.

## Payload

### 0.3 — 2026-08-11 — idle-screen start, and OTA-ready

The handset can start a session from either of the booth's idle screens.

**Problem.** The connected loop only ever transmitted from inside the
`videoReady`, `videoCountdown`, `videoCapture` and `: gallery` branches. Once
the booth pushed `s:<folder>: standby`, `state` matched none of them, so a
handset lift was read into `offHook` and silently discarded. The `allowStart`
latch made it worse: it is only ever set inside the start branch, so an idle
booth could not even re-arm the handset when the guest hung up. Net effect —
after the booth idled, the handset was completely dead until someone touched
the booth screen.

Reported by a customer as *"after exactly 8 minutes of inactivity the live
preview freezes and picking up the phone does not trigger a session"*.

**Change.** `: standby` and `: powerSaving` are treated exactly like
`videoReady`. The handset knows the booth is idle and deliberately sends
`videoStart`; the booth wakes itself and starts the countdown.

**Requires a matching booth build** — `videoStart` has to be accepted while the
booth is idle, which it was not before.

Also the first payload shaped for the core: entry point is `run(env)`, it
reports its version on connect, routes `u:` lines to the shared updater, and
ignores the cradle mid-transfer so a lift cannot start a session the guest is
not there for while firmware is being written.

Distributed as a **GitHub release in this repository**. Breeze Booth lists the
releases published here and installs the one an operator selects in *Settings →
Devices → Handset Firmware*; the version it reports comes from `APP_VERSION` in
the payload, not from the release tag.

### 0.2 — ≤2024-01-20 — baseline

The version found in the field, captured verbatim before any edit. Video-only,
idle screens unhandled.

## Known gaps

- **Video only.** There is no `stillsReady` or `gifReady` branch, so the
  handset cannot start a stills or GIF session. The booth side is already
  general, so this is a payload-only change whenever it is wanted.
- **The core cannot update itself.** `u:begin` writes `app.py` and nothing
  else. Changing the core means USB. This is deliberate — the code that
  performs updates is the worst possible thing to update remotely — but it does
  mean a core bug is a fleet-wide site visit.
