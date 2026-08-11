# Firmware update protocol

The handset is updated over the Nordic UART link the booth already uses for
status and commands. There is no second BLE service and no extra CircuitPython
library — the update is just more lines on the same wire.

Everything here is newline-terminated ASCII. The booth is the BLE central and
**always initiates**; the handset only ever replies. Every command gets exactly
one reply, and the booth must wait for it before sending the next line. That
acknowledgement *is* the flow control: it keeps a single line in flight, so the
handset's UART buffer can never overflow no matter how the two ends are paced.

Every line in both directions starts with `u:`. Nothing else the booth or the
handset sends uses that prefix, so firmware traffic can never be mistaken for a
touchscreen action name.

## Commands

| Booth → handset | Handset → booth | Meaning |
| --- | --- | --- |
| `u:?` | `u:v <core> <app> <status> <fs>` | Report versions and readiness |
| `u:begin <size> <crc32>` | `u:ok begin <maxdata>` | Open a transfer of `size` bytes with the given CRC-32 |
| `u:d <base64>` | `u:ok <received>` | One chunk. `received` is the running byte count |
| `u:end` | `u:ok end` | Close and verify. The payload is staged, not live |
| `u:apply` | `u:ok apply` | Swap the payload in and reset. **The link drops here** |
| `u:confirm` | `u:ok confirm` | Accept the running payload permanently |
| `u:abort` | `u:ok abort` | Discard a transfer in progress |

`<size>` is decimal, `<crc32>` is 8 lowercase hex digits (IEEE CRC-32, the same
value `zlib.crc32` produces), and `<base64>` is standard base64 of a slice of
the payload, no line breaks.

`<maxdata>` is the largest base64 payload the handset will accept on one `u:d`
line. **Read it from the reply rather than assuming** — it exists so the chunk
size can change without a coordinated release on both ends, and it has already
had to.

Today it is **52**, giving a 57-byte line (`u:d ` + 52 + newline) and 39 payload
bytes per line. That is dictated by the UART receive buffer, which is a fixed 64
bytes: `buffer_size` is a parameter of the `StreamIn`/`StreamOut`
characteristics *inside* `UARTService`, fixed where the class is defined, so
passing it to the constructor raises `TypeError` and enlarging it would mean
redefining the whole service. Since the flow control keeps one line in flight,
the whole line has to fit in that buffer or bytes are dropped silently.

The handset also sends `u:v …` unprompted immediately after connecting, so the
booth normally never needs `u:?` at all.

## Version reply

```
u:v 1.0.0 0.3 ok rw
        │   │   │  └── rw | offhook | noboot — see below
        │   │   └───── ok | pending | recovery
        │   └───────── payload version, or "none" in recovery mode
        └───────────── immutable-core version
```

The last field says who owns the filesystem, and when it is not the script,
**why** — because the two read-only causes need opposite actions and a booth
cannot tell them apart on its own:

| Value | Meaning | What fixes it |
| --- | --- | --- |
| `rw` | The script owns the filesystem | — |
| `offhook` | `boot.py` is installed but the board was powered up off the hook, so CIRCUITPY belongs to USB | Hang the handset up and power-cycle |
| `noboot` | `boot.py` was never installed, so the remount never happens | Finish the USB install; no power-cycle will help |

A booth that does not recognise the value must treat it as not writable.
`u:begin` is refused for anything but `rw` regardless.

`pending` means a payload was applied but not yet confirmed. The booth should
send `u:confirm` once it is satisfied the handset works — see below.

`recovery` means the payload is broken and the unit is running the immutable
core only. It cannot start sessions, but it can still be updated. **A booth
seeing `recovery` should push firmware without waiting to be asked.**

## Errors

Any command can be answered with `u:err <reason>` instead:

| Reason | Meaning |
| --- | --- |
| `readonly` | The script cannot write the filesystem — see the `offhook`/`noboot` values above |
| `notarmed` | `REQUIRE_PHYSICAL_ARM` is on and nobody has cycled the cradle |
| `badbegin`, `size` | Malformed or implausible `u:begin` |
| `open`, `write`, `apply` | Filesystem refused the operation |
| `nosession` | `u:d` or `u:end` with no transfer open |
| `b64` | Chunk was not decodable base64 |
| `overrun` | More data arrived than `u:begin` declared |
| `short <n>` | `u:end` with fewer bytes than declared |
| `crc` | Everything arrived, but the CRC-32 does not match |
| `nostaging` | `u:apply` with nothing verified and staged |
| `badcmd` | Unrecognised `u:` line |

`b64`, `overrun`, `write` and every `u:end`/`u:apply` failure discard the
staged file. After any error the booth can simply start again with `u:begin` —
a fresh transfer always supersedes an abandoned one.

## Why the payload is staged, not written in place

`u:end` only verifies. Nothing on the board changes until `u:apply`, so a
transfer that is interrupted, corrupted, or aborted leaves the running firmware
untouched. There is no window in which the handset holds a half-written
`app.py`.

## Who starts an update

An operator, from *Settings → Devices → Handset Firmware* in Breeze Booth. The
booth lists the releases published in this repository and writes the one they
pick; it never chooses for them, and it never installs anything unprompted.

The single exception is `u:confirm`, which the booth sends by itself whenever a
handset reports `pending` — see below. That one is not a decision, it is the
second half of an install that has already happened.

Installs are refused while the booth is mid-session. A transfer takes the
handset offline for the better part of a minute.

## The confirm step

`u:apply` is not the end of the update. It renames the current payload to
`app_prev.py`, moves the new one into place, writes a `pending` marker and
resets the board.

On each boot the launcher increments the marker's boot count. If the payload
raises, or if it reaches three boots without the booth sending `u:confirm`, the
launcher restores `app_prev.py` and resets.

So the booth has to actively accept a new payload for it to stick:

1. `u:apply` → link drops
2. the handset reboots and reconnects, announcing `u:v … pending …`
3. the booth satisfies itself the version is the expected one
4. `u:confirm` → the marker is deleted and the payload is permanent

Doing nothing at step 4 reverts the handset. That is deliberate: a payload that
starts cleanly but cannot talk to the booth — wrong advertised name, a hang in
the connect loop — is exactly as unreachable as one that crashed, and the boot
counter is the only thing that catches it.

## Worked example

```
handset → u:v 1.0.0 0.3 ok rw
booth   → u:begin 5504 9620e449
handset → u:ok begin 52
booth   → u:d IyBDb3B5cmlnaHQgKEMpIDIwMjQtMjAyNiBXZSBGbHk=
handset → u:ok 39
booth   → u:d IEtpdGVzIFB0eSBMdGQgKHd3dy5icmVlemVzeXMuY28=
handset → u:ok 78
          … 139 more chunks …
booth   → u:end
handset → u:ok end
booth   → u:apply
handset → u:ok apply
          *** disconnect, reboot ***
handset → u:v 1.0.0 0.4 pending rw
booth   → u:confirm
handset → u:ok confirm
```

## Timing

The payload is a few KB. The booth chunks its BLE writes to 16 bytes with
write-with-response, so throughput is bound by bytes on the wire rather than by
the number of protocol lines: roughly one 16-byte write per connection event.
A 5.5 KB payload is ~142 lines of 57 bytes, so on the order of 15–25 seconds.
Budget for a minute including the reboot and reconnect, and do not attempt an
update outside an idle booth state.

## Security

There is no authentication. Any BLE central that knows this protocol and is in
range can replace a handset's payload.

That was a deliberate trade, and it is worth being explicit about what it does
and does not put at risk. The handset holds no secrets, has no network of its
own, and reads a single microswitch; the practical worst case of a hostile push
is a handset that stops working, which a USB visit fixes. Weighed against that,
requiring a human at the booth for every update would have defeated the point
of building OTA at all.

CircuitPython's BLE stack does not usefully expose pairing or bonding, so the
alternative was a shared secret — which, in a public repository, is not a
secret. What the firmware offers instead is `REQUIRE_PHYSICAL_ARM` in
`updater.py`: with it enabled, `u:begin` is refused until somebody lifts and
replaces the handset, which cannot be done over the air. It is off by default.
Turn it on for a venue where an untrusted person could plausibly be in BLE
range with intent, and accept that updates there become an attended operation.
