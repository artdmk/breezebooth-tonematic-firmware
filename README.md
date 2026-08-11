# Tonematic Pro handset firmware

CircuitPython firmware for the Tonematic Pro — the retro telephone handset used
as a video-guestbook trigger for [Breeze Booth for iPad](https://www.breezesys.com).
A guest lifts the handset to start recording a message and replaces it to stop.

The handset is an Adafruit Feather nRF52840 Express running CircuitPython
8.2.9, presenting itself as a Nordic UART peripheral. The booth connects to it
as a BLE central and pushes booth status; the handset decides for itself when
to send commands back.

This repository is the canonical source for that firmware and the release
channel for it. From v1.0 of the core, handsets can be **updated over BLE by
the booth they are already paired with** — no USB, no site visit.

## Layout

```
firmware/
  boot.py        immutable core — decides who owns the filesystem
  code.py        immutable core — launcher, rollback, recovery
  nus.py         immutable core — BLE bring-up and advertised name
  updater.py     immutable core — the update protocol
  recovery.py    immutable core — minimal loop when the payload is broken
  app.py         the payload — booth logic; this is what ships over the air
docs/
  PROTOCOL.md    the wire protocol
  RECOVERY.md    LED codes, failure modes, getting CIRCUITPY back
tools/
  test_updater.py  host-side tests for the protocol
```

The split is the whole design. **The core is installed once over USB and never
shipped over the air; only `app.py` is.** Everything that decides whether a
handset can be reached at all — the advertised name, the BLE bring-up, the
updater itself, the rollback logic — lives in the core, so a bad payload cannot
take a unit off the air.

## Updating a handset over the air

**Releases in this repository are what the booth installs.** In Breeze Booth,
go to *Settings → Devices → Handset Firmware*: while a Tonematic handset is
connected the section shows what it is running and offers *Install firmware
version…*, which lists the releases published here and writes the one you pick.

The booth reads the release list from the GitHub API and fetches
`firmware/app.py` from that release's tag, so **a release only has to exist** —
there is no asset to remember to attach. The immutable core is deliberately not
fetchable; it only ever installs over USB.

Nothing is pushed automatically. Which release a venue runs is an operator's
decision, so a handset stays on what it has until somebody chooses otherwise.
The one thing the booth does on its own is *confirm* a payload it just
installed, which is not optional — see below.

Sequence and error handling are in [docs/PROTOCOL.md](docs/PROTOCOL.md). The
short version: a payload is transferred, CRC-checked and staged before anything
is swapped, and then has to survive three boots *and* be confirmed by the booth
or it rolls back on its own.

### Publishing a release

1. Update `firmware/app.py`, bumping `APP_VERSION`.
2. Update `CHANGELOG.md`.
3. Tag and publish a GitHub release on that tag.

The booth reads the version from `APP_VERSION` in the payload, not from the tag,
so the two are allowed to differ — the tag names the release, `APP_VERSION`
names the firmware.

## Installing the core (one USB visit per unit)

Only needed once, for units that have never run a core build.

```sh
python3 tools/install.py            # or --no-boot to install everything but boot.py
```

**Do not drag the files across in Finder.** CircuitPython auto-reloads after
*every* file that lands, so a plain copy runs a half-installed core: Finder
copies alphabetically, `code.py` arrives third, and `nus.py`, `recovery.py` and
`updater.py` are not there yet. Each reload also interrupts the copy. The board
ends up red-blinking with a partial filesystem — and if `boot.py` made it
across, the next reset hands the filesystem to the script and CIRCUITPY goes
read-only over USB, so copying a fix silently does nothing.

`tools/install.py` sidesteps that by holding the REPL, which suspends
auto-reload for the whole copy, writing the files in dependency order, then
starting the core once and reading the console back to confirm it worked.

If a unit already runs a core, **hold the handset off the hook while plugging in
USB** — otherwise `boot.py` gives the filesystem to the script and there is
nothing to copy to. See [docs/RECOVERY.md](docs/RECOVERY.md).

The board comes up green (connected) or red (advertising). Purple/white means
recovery mode — the payload did not start. A slow, even red blink means the
core itself is incomplete; the console says which file is missing.

After this, the unit never needs USB again unless the core itself changes.

## Testing

The updater is the one piece that cannot be fixed over the air, so it is tested
off-board before it reaches a handset:

```sh
python3 tools/test_updater.py
```

This stubs the CircuitPython-only modules and drives the protocol end to end —
transfers, corruption, truncation, overrun, read-only filesystems, rollback.

## Compatibility

| | |
| --- | --- |
| Board | Adafruit Feather nRF52840 Express (`feather_nrf52840_express`) |
| Runtime | Adafruit CircuitPython **8.2.9** — pinned |
| Libraries | `adafruit_ble`, `adafruit_led_animation`, `neopixel` |
| Booth | Breeze Booth for iPad with handset firmware-update support |

The runtime pin matters: the bundled libraries are `.mpy` bytecode and
CircuitPython enforces an `.mpy` compatibility version.

## License

Source-available, not open source — see [LICENSE](LICENSE).

In short: you may read, modify, install and redistribute this firmware for
Tonematic Pro handsets you own or operate, keeping the notices intact and
marking modified versions as unofficial. You may not use the Breeze or
Tonematic names to promote a modified version, or ship it as firmware for other
hardware, without written permission. No warranty — flashing a device can brick
it until you recover it physically.

Copyright © 2024–2026 We Fly Kites Pty Ltd (www.breezesys.com).
