#!/usr/bin/env python3
"""Host-side tests for firmware/updater.py.

The updater is the one piece of the immutable core that cannot be fixed over
the air, so it gets tested off-board before it ever reaches a handset. The
CircuitPython-only modules it imports (`storage`, `microcontroller`) are
stubbed, and the filesystem paths are pointed at a temp directory.

    python3 tools/test_updater.py
"""

import base64
import binascii
import os
import shutil
import sys
import tempfile
import types
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.join(os.path.dirname(HERE), "firmware")

# --- CircuitPython stubs ---------------------------------------------------

_readonly = False
_resets = []


class _Mount:
    @property
    def readonly(self):
        return _readonly


storage = types.ModuleType("storage")
storage.getmount = lambda path: _Mount()
storage.remount = lambda path, readonly: None
sys.modules["storage"] = storage

microcontroller = types.ModuleType("microcontroller")
microcontroller.reset = lambda: _resets.append(True)
sys.modules["microcontroller"] = microcontroller


class _Runtime:
    autoreload = True


supervisor = types.ModuleType("supervisor")
supervisor.runtime = _Runtime()
sys.modules["supervisor"] = supervisor

sys.path.insert(0, FIRMWARE)
import updater  # noqa: E402


class FakeUART:
    def __init__(self):
        self.lines = []

    def write(self, text):
        self.lines.append(text.strip())

    @property
    def last(self):
        return self.lines[-1] if self.lines else None


# --- harness ---------------------------------------------------------------

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, detail))
        FAILURES.append(name)


def sandbox():
    """Point the updater at a temp directory and return it."""
    d = tempfile.mkdtemp(prefix="tonematic-")
    updater.TARGET = os.path.join(d, "app.py")
    updater.STAGING = os.path.join(d, "app_new.py")
    updater.PREVIOUS = os.path.join(d, "app_prev.py")
    updater.PENDING = os.path.join(d, "pending")
    updater.BOOT = os.path.join(d, "boot.py")
    return d


def send_payload(up, uart, payload, crc=None, chunk=None):
    """Drive a full begin/d.../end, honouring the advertised chunk size."""
    if crc is None:
        crc = zlib.crc32(payload) & 0xFFFFFFFF
    up.handle("u:begin %d %08x" % (len(payload), crc), uart)
    if not uart.last.startswith("u:ok begin"):
        return uart.last
    max_b64 = int(uart.last.split()[-1]) if chunk is None else chunk
    raw = (max_b64 // 4) * 3
    for i in range(0, len(payload), raw):
        up.handle("u:d " + base64.b64encode(payload[i:i + raw]).decode(), uart)
        if not uart.last.startswith("u:ok"):
            return uart.last
    up.handle("u:end", uart)
    return uart.last


# --- tests -----------------------------------------------------------------

def test_crc_matches_zlib():
    print("crc32 agrees with zlib")
    vectors = [b"", b"a", b"123456789", os.urandom(4174)]
    for v in vectors:
        check("len %d" % len(v), updater.crc32(v) == (zlib.crc32(v) & 0xFFFFFFFF))
    # streaming must equal one-shot, since transfers are folded in per chunk
    d = os.urandom(3000)
    running = 0xFFFFFFFF
    for i in range(0, len(d), 180):
        running = updater.crc32_update(d[i:i + 180], running)
    check("streamed == one-shot",
          updater.crc32_final(running) == (zlib.crc32(d) & 0xFFFFFFFF))


def test_happy_path():
    print("full transfer, apply, confirm")
    d = sandbox()
    try:
        with open(updater.TARGET, "wb") as f:
            f.write(b"# old payload\n")
        payload = b"# new payload\n" + os.urandom(2000)
        up = updater.Updater(app_version="0.3")
        uart = FakeUART()

        check("version reply", up.handle("u:?", uart) and
              uart.last == "u:v %s 0.3 ok rw" % updater.CORE_VERSION, uart.last)

        result = send_payload(up, uart, payload)
        check("end acknowledged", result == "u:ok end", result)
        check("staging matches", open(updater.STAGING, "rb").read() == payload)
        check("target untouched until apply",
              open(updater.TARGET, "rb").read() == b"# old payload\n")

        del _resets[:]
        up.handle("u:apply", uart)
        check("apply acknowledged", "u:ok apply" in uart.lines)
        check("reset requested", len(_resets) == 1)
        check("target replaced", open(updater.TARGET, "rb").read() == payload)
        check("previous kept",
              open(updater.PREVIOUS, "rb").read() == b"# old payload\n")
        check("staging consumed", not updater.exists(updater.STAGING))

        pending = updater.read_pending()
        check("pending recorded", pending is not None and pending[1] == 0, pending)

        up.handle("u:confirm", uart)
        check("confirm acknowledged", uart.last == "u:ok confirm")
        check("pending cleared", updater.read_pending() is None)
    finally:
        shutil.rmtree(d)


def test_corruption_is_rejected():
    print("a corrupted transfer never reaches app.py")
    d = sandbox()
    try:
        with open(updater.TARGET, "wb") as f:
            f.write(b"# good\n")
        payload = os.urandom(1024)
        up = updater.Updater()
        uart = FakeUART()

        wrong = (zlib.crc32(payload) ^ 0xFFFF) & 0xFFFFFFFF
        result = send_payload(up, uart, payload, crc=wrong)
        check("crc mismatch rejected", result == "u:err crc", result)
        check("staging discarded", not updater.exists(updater.STAGING))

        up.handle("u:apply", uart)
        check("apply refused without staging", uart.last == "u:err nostaging",
              uart.last)
        check("target untouched", open(updater.TARGET, "rb").read() == b"# good\n")
    finally:
        shutil.rmtree(d)


def test_truncated_transfer():
    print("a transfer that stops early is rejected")
    d = sandbox()
    try:
        payload = os.urandom(900)
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin %d %08x" % (len(payload), zlib.crc32(payload) & 0xFFFFFFFF), uart)
        up.handle("u:d " + base64.b64encode(payload[:180]).decode(), uart)
        up.handle("u:end", uart)
        check("short transfer rejected", uart.last.startswith("u:err short"),
              uart.last)
        check("staging discarded", not updater.exists(updater.STAGING))
    finally:
        shutil.rmtree(d)


def test_overrun_is_rejected():
    print("more data than declared is rejected")
    d = sandbox()
    try:
        payload = os.urandom(300)
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin 100 %08x" % (zlib.crc32(payload) & 0xFFFFFFFF), uart)
        up.handle("u:d " + base64.b64encode(payload).decode(), uart)
        check("overrun rejected", uart.last == "u:err overrun", uart.last)
        check("staging discarded", not updater.exists(updater.STAGING))
    finally:
        shutil.rmtree(d)


def test_bad_base64():
    print("undecodable data aborts the transfer")
    d = sandbox()
    try:
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin 100 00000000", uart)
        up.handle("u:d !!!!not base64!!!!", uart)
        check("rejected", uart.last == "u:err b64", uart.last)
    finally:
        shutil.rmtree(d)


def test_readonly_filesystem():
    print("a USB-writable board refuses updates instead of half-doing one")
    global _readonly
    d = sandbox()
    _readonly = True
    try:
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin 10 00000000", uart)
        check("begin refused", uart.last == "u:err readonly", uart.last)
    finally:
        _readonly = False
        shutil.rmtree(d)


def test_readonly_reason_is_distinguished():
    print("read-only says WHICH cause, because the fixes are opposite")
    global _readonly
    d = sandbox()
    try:
        up = updater.Updater()
        uart = FakeUART()

        up.handle("u:?", uart)
        check("writable reports rw", uart.last.endswith(" rw"), uart.last)

        # boot.py present but the board came up off the hook: USB owns the
        # filesystem, and hanging it up and power-cycling fixes it.
        _readonly = True
        with open(updater.BOOT, "w") as f:
            f.write("# boot\n")
        up.handle("u:?", uart)
        check("off-hook reports offhook", uart.last.endswith(" offhook"), uart.last)

        # boot.py never installed: the remount never happens at all, so no
        # amount of power-cycling will help.
        os.remove(updater.BOOT)
        up.handle("u:?", uart)
        check("missing boot.py reports noboot", uart.last.endswith(" noboot"),
              uart.last)
    finally:
        _readonly = False
        shutil.rmtree(d)


def test_restart_supersedes_abandoned_transfer():
    print("a new begin supersedes an abandoned transfer")
    d = sandbox()
    try:
        payload = os.urandom(600)
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin 5000 00000000", uart)
        up.handle("u:d " + base64.b64encode(os.urandom(180)).decode(), uart)
        result = send_payload(up, uart, payload)
        check("second transfer completes", result == "u:ok end", result)
        check("staging is the second payload",
              open(updater.STAGING, "rb").read() == payload)
    finally:
        shutil.rmtree(d)


def test_rollback():
    print("rollback restores the previous payload")
    d = sandbox()
    try:
        with open(updater.TARGET, "wb") as f:
            f.write(b"# broken\n")
        with open(updater.PREVIOUS, "wb") as f:
            f.write(b"# working\n")
        updater.write_pending("deadbeef", 3)
        check("rollback reports success", updater.rollback() is True)
        check("target restored",
              open(updater.TARGET, "rb").read() == b"# working\n")
        check("pending cleared", updater.read_pending() is None)
        check("no previous left", not updater.exists(updater.PREVIOUS))
        check("second rollback is a no-op", updater.rollback() is False)
    finally:
        shutil.rmtree(d)


def test_busy_flag():
    print("busy is set only for the duration of a transfer")
    d = sandbox()
    try:
        payload = os.urandom(400)
        up = updater.Updater()
        uart = FakeUART()
        check("idle before", up.busy is False)
        up.handle("u:begin %d %08x" % (len(payload), zlib.crc32(payload) & 0xFFFFFFFF), uart)
        check("busy during", up.busy is True)
        up.handle("u:d " + base64.b64encode(payload).decode(), uart)
        up.handle("u:end", uart)
        check("idle after", up.busy is False)
    finally:
        shutil.rmtree(d)


def test_non_firmware_lines_pass_through():
    print("booth traffic is not swallowed")
    d = sandbox()
    try:
        up = updater.Updater()
        uart = FakeUART()
        check("status line ignored",
              up.handle("s:Some Profile/portrait/.: videoReady", uart) is False)
        check("device name ignored", up.handle("i:iPad", uart) is False)
        check("nothing written", uart.lines == [])
    finally:
        shutil.rmtree(d)


def test_physical_arm():
    print("REQUIRE_PHYSICAL_ARM gates begin until the cradle is cycled")
    d = sandbox()
    updater.REQUIRE_PHYSICAL_ARM = True
    try:
        up = updater.Updater()
        uart = FakeUART()
        up.handle("u:begin 10 00000000", uart)
        check("refused unarmed", uart.last == "u:err notarmed", uart.last)
        up.arm()
        up.handle("u:begin 10 00000000", uart)
        check("accepted once armed", uart.last.startswith("u:ok begin"),
              uart.last)
    finally:
        updater.REQUIRE_PHYSICAL_ARM = False
        shutil.rmtree(d)


def main():
    for test in (test_crc_matches_zlib, test_happy_path,
                 test_corruption_is_rejected, test_truncated_transfer,
                 test_overrun_is_rejected, test_bad_base64,
                 test_readonly_filesystem, test_readonly_reason_is_distinguished,
                 test_restart_supersedes_abandoned_transfer, test_rollback,
                 test_busy_flag, test_non_firmware_lines_pass_through,
                 test_physical_arm):
        test()
    print()
    if FAILURES:
        print("%d FAILED: %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
