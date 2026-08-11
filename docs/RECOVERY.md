# Recovery

Three things can go wrong with a handset. Two of them fix themselves.

## The LED tells you which

| Colour | Meaning |
| --- | --- |
| Red | Not connected, handset on hook |
| Orange | Not connected, handset off hook |
| Green | Connected, handset on hook |
| Blue | Connected, handset off hook |
| **Purple/white alternating** | **Recovery mode — the payload is broken** |
| **Solid white** | **Recovery mode, booth connected — push firmware** |
| **Slow even red blink (1 s on, 1 s off)** | **The core itself is incomplete — see below** |

Red through blue are normal. Anything purple or white means the unit needs
firmware, and can still receive it over the air.

Note the difference between steady red (normal: not connected, on hook) and a
slow even red blink (`panic()` in `code.py`: a core file is missing or broken).
The blink is deliberately unhurried so it does not read as activity.

## 0. A core file is missing — copy it back

Almost always the aftermath of copying the core by hand instead of using
`tools/install.py`. The console names the missing file. Auto-reload is left on
precisely so this is fixable: copy the file across and the board restarts
itself.

If CIRCUITPY is read-only, `boot.py` landed and took the filesystem. Unplug,
hold the handset off the hook, plug back in, then re-run `tools/install.py`.

## 1. A bad payload that crashes — automatic

The launcher catches it, restores `app_prev.py` and resets. The handset comes
back on the previous version within a few seconds. Nothing to do.

## 2. A bad payload that runs but cannot be reached — automatic

Harder, and the reason the confirm step exists. A payload can start cleanly and
still be useless: advertising under the wrong name, hanging before the connect
loop, never reading the cradle.

The launcher counts boots against the `pending` marker the update wrote. Three
boots without the booth sending `u:confirm` and it restores `app_prev.py`. The
handset comes back on the previous version within about a minute.

## 3. No previous payload to fall back to — recovery mode

Only reachable on a unit whose *first* payload was bad, since any updated unit
has an `app_prev.py`. The launcher starts `recovery.py`: no cradle, no booth
commands, but it advertises under the normal `BreezeAudio_…` name and still
answers the update protocol.

A handset in recovery is broken but reachable. Push a working payload and it
comes back. The purple/white LED is how you spot one on site without a laptop.

## Getting CIRCUITPY back over USB

Normal service needs the script to own the filesystem, which means the USB host
does not — plug a working handset into a computer and CIRCUITPY mounts
read-only. Drag-and-drop does not work, and that is expected.

To get the stock, writable drive back:

> **Lift the handset out of the cradle and hold it there while you plug in USB.**

`boot.py` reads the cradle switch before anything else runs. Off the hook, it
skips the remount and the board behaves exactly as a stock CircuitPython board:
CIRCUITPY writable, drag-and-drop, REPL over serial. Hang the handset up and
power-cycle to return to normal service.

This is the fallback that makes everything else safe to attempt. It works even
when `code.py`, `app.py` and `updater.py` are all broken, because it happens
before any of them run.

## Last resort: rebuilding a bare board

If `boot.py` itself is gone or the filesystem is corrupt, reflash:

1. Double-tap reset → the `FEATHER_BOOT` volume appears.
2. Drop CircuitPython **8.2.9** for `feather_nrf52840_express` onto it.
3. Copy `lib/` from the archived volume dump onto the new CIRCUITPY, then this
   repo's `firmware/` contents.

Keep the runtime at 8.2.9. The bundled libraries are `.mpy` bytecode and
CircuitPython enforces an `.mpy` compatibility version, so a different major
release refuses to import them unless you also re-fetch a matching Adafruit
bundle.

## Diagnostics

A payload failure writes a traceback to `/last_error.txt` on CIRCUITPY. Read it
by booting off-hook and mounting the drive. It is best-effort — if the
filesystem is what failed, there will be nothing there.
