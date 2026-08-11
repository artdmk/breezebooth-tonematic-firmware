# Tonematic Pro - BLE firmware updater.
#
# Part of the immutable core. Shared by app.py (normal operation) and
# recovery.py (when the payload is broken) so there is exactly one
# implementation of the wire protocol.
#
# The protocol runs over the existing Nordic UART link the booth already uses
# for status and commands - no extra service, no extra library. Every line is
# newline-terminated ASCII, the booth always initiates, and the handset
# acknowledges every line so the booth never outruns the UART buffer.
#
# See docs/PROTOCOL.md for the wire format.

import binascii
import os
import storage
import time
import microcontroller
import supervisor

CORE_VERSION = "1.0.0"

TARGET = "/app.py"
STAGING = "/app_new.py"
PREVIOUS = "/app_prev.py"
PENDING = "/pending"
BOOT = "/boot.py"

# Largest base64 payload accepted on one `u:d` line. Reported to the booth in
# the `u:begin` reply so the sender adapts instead of guessing.
#
# Bounded by the UART receive buffer, which is a fixed 64 bytes and cannot be
# enlarged (see nus.RX_BUFFER_SIZE). The flow control keeps a single line in
# flight, so the whole of the longest one has to fit: `u:d ` + 52 base64 chars
# + newline = 57 bytes. 52 is a multiple of 4, so no chunk but the last carries
# base64 padding, and each line therefore moves 39 payload bytes.
#
# Raising this without also enlarging the buffer silently drops bytes.
MAX_DATA = 52

# Refuse a payload larger than this. Guards against a runaway transfer filling
# the filesystem; the real payload is a few KB.
MAX_SIZE = 65536

# When True the handset only accepts `u:begin` if a human lifted and replaced
# the handset since power-up, proving physical presence. Off by default: the
# booth updates its own handset unattended, and the practical worst case of an
# unauthorised push is a bricked handset, not data loss - it holds no secrets
# and has no network of its own. Turn it on if a venue needs it.
REQUIRE_PHYSICAL_ARM = False

# CRC-32 (IEEE, reflected) via the 16-entry nibble table. A 256-entry table
# would be faster but this runs once per transfer over a few KB, and the small
# table keeps the immutable core readable. Verified against zlib.crc32.
_CRC_TABLE = (
    0x00000000, 0x1DB71064, 0x3B6E20C8, 0x26D930AC,
    0x76DC4190, 0x6B6B51F4, 0x4DB26158, 0x5005713C,
    0xEDB88320, 0xF00F9344, 0xD6D6A3E8, 0xCB61B38C,
    0x9B64C2B0, 0x86D3D2D4, 0xA00AE278, 0xBDBDF21C,
)


def crc32_update(data, crc=0xFFFFFFFF):
    """Fold `data` into a running CRC. Seed with 0xFFFFFFFF, finalise with
    crc32_final()."""
    for b in data:
        crc ^= b
        crc = (crc >> 4) ^ _CRC_TABLE[crc & 0x0F]
        crc = (crc >> 4) ^ _CRC_TABLE[crc & 0x0F]
    return crc


def crc32_final(crc):
    return crc ^ 0xFFFFFFFF


def crc32(data):
    return crc32_final(crc32_update(data))


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def writable():
    """True when boot.py remounted the filesystem for the script."""
    try:
        return not storage.getmount("/").readonly
    except Exception:
        return False


def filesystem_state():
    """What the booth is told about the filesystem, and why.

    Read-only has two quite different causes and the fix differs, so do not
    make the booth guess:

      rw      the script owns the filesystem; updates are possible
      offhook boot.py is installed but the board was powered up off the hook,
              so CIRCUITPY belongs to USB - hang it up and power-cycle
      noboot  boot.py was never installed, so the remount never happens at all
              and no power-cycle will help - finish the USB install
    """
    if writable():
        return "rw"
    return "offhook" if exists(BOOT) else "noboot"


def read_pending():
    """(crc_hex, boot_count) for a staged-but-unconfirmed update, else None."""
    try:
        with open(PENDING, "r") as f:
            parts = f.read().strip().split()
        return (parts[0], int(parts[1]))
    except Exception:
        return None


def write_pending(crc_hex, boots):
    with open(PENDING, "w") as f:
        f.write("%s %d\n" % (crc_hex, boots))


def set_autoreload(on):
    """Auto-reload is left ON in normal service, deliberately: it is what makes
    'copy the missing file over USB' fix a board whose core is incomplete. It is
    suspended only for the duration of a transfer, so writing app.py cannot
    restart the script mid-update."""
    try:
        supervisor.runtime.autoreload = on
    except Exception:
        pass


def rollback():
    """Put the previous payload back. Returns True if there was one."""
    if not exists(PREVIOUS) or not writable():
        remove(PENDING)
        return False
    remove(TARGET)
    os.rename(PREVIOUS, TARGET)
    remove(PENDING)
    return True


class Updater:
    """Consumes `u:` lines. Everything else is the caller's business."""

    def __init__(self, app_version="none", status="ok"):
        self.app_version = app_version
        self.status = status          # ok | pending | recovery
        self.armed = not REQUIRE_PHYSICAL_ARM
        self._reset()

    # ---- transfer state -------------------------------------------------

    def _reset(self):
        if getattr(self, "_f", None) is not None:
            try:
                self._f.close()
            except Exception:
                pass
            set_autoreload(True)
        self._f = None
        self._expected = 0
        self._want_crc = 0
        self._got = 0
        self._crc = 0xFFFFFFFF

    @property
    def busy(self):
        """True mid-transfer. The caller should stop sending booth commands so
        a handset lift cannot start a session during an update."""
        return self._f is not None

    def arm(self):
        """Called by the payload when the hook switch is cycled, satisfying
        REQUIRE_PHYSICAL_ARM."""
        self.armed = True

    # ---- protocol -------------------------------------------------------

    def handle(self, line, uart):
        """Handle one line. Returns True if it was ours."""
        if not line.startswith("u:"):
            return False
        body = line[2:].strip()

        if body == "?":
            self._send(uart, "v %s %s %s %s" % (
                CORE_VERSION, self.app_version, self.status,
                filesystem_state()))
        elif body.startswith("begin"):
            self._begin(body, uart)
        elif body.startswith("d "):
            self._data(body[2:], uart)
        elif body == "end":
            self._end(uart)
        elif body == "apply":
            self._apply(uart)
        elif body == "confirm":
            remove(PENDING)
            self.status = "ok"
            self._send(uart, "ok confirm")
        elif body == "abort":
            self._reset()
            remove(STAGING)
            self._send(uart, "ok abort")
        else:
            self._send(uart, "err badcmd")
        return True

    def _send(self, uart, text):
        uart.write("u:" + text + "\n")

    def _begin(self, body, uart):
        # A fresh begin always supersedes an abandoned transfer.
        self._reset()
        remove(STAGING)

        if not writable():
            self._send(uart, "err readonly")
            return
        if not self.armed:
            self._send(uart, "err notarmed")
            return

        parts = body.split()
        if len(parts) != 3:
            self._send(uart, "err badbegin")
            return
        try:
            size = int(parts[1])
            want = int(parts[2], 16)
        except ValueError:
            self._send(uart, "err badbegin")
            return
        if size <= 0 or size > MAX_SIZE:
            self._send(uart, "err size")
            return

        try:
            self._f = open(STAGING, "wb")
        except OSError:
            self._send(uart, "err open")
            return
        set_autoreload(False)
        self._expected = size
        self._want_crc = want
        self._send(uart, "ok begin %d" % MAX_DATA)

    def _data(self, b64, uart):
        if self._f is None:
            self._send(uart, "err nosession")
            return
        try:
            chunk = binascii.a2b_base64(b64)
        except Exception:
            self._reset()
            remove(STAGING)
            self._send(uart, "err b64")
            return
        if self._got + len(chunk) > self._expected:
            self._reset()
            remove(STAGING)
            self._send(uart, "err overrun")
            return
        try:
            self._f.write(chunk)
        except OSError:
            self._reset()
            remove(STAGING)
            self._send(uart, "err write")
            return
        self._got += len(chunk)
        self._crc = crc32_update(chunk, self._crc)
        self._send(uart, "ok %d" % self._got)

    def _end(self, uart):
        if self._f is None:
            self._send(uart, "err nosession")
            return
        try:
            self._f.close()
        except OSError:
            pass
        self._f = None
        set_autoreload(True)

        if self._got != self._expected:
            remove(STAGING)
            self._send(uart, "err short %d" % self._got)
            return
        if crc32_final(self._crc) != self._want_crc:
            remove(STAGING)
            self._send(uart, "err crc")
            return
        self._send(uart, "ok end")

    def _apply(self, uart):
        if not exists(STAGING):
            self._send(uart, "err nostaging")
            return
        if not writable():
            self._send(uart, "err readonly")
            return
        try:
            remove(PREVIOUS)
            if exists(TARGET):
                os.rename(TARGET, PREVIOUS)
            os.rename(STAGING, TARGET)
            write_pending("%08x" % self._want_crc, 0)
        except OSError:
            self._send(uart, "err apply")
            return
        self._send(uart, "ok apply")
        # Give the reply time to leave before the radio goes down. A hard reset
        # rather than supervisor.reload() so the booth unambiguously sees a
        # disconnect and the radio comes back clean.
        time.sleep(0.5)
        microcontroller.reset()
