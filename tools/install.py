#!/usr/bin/env python3
"""Install the immutable core onto a Tonematic Pro handset over USB.

Copying the core by dragging files in Finder does not work, and fails in a way
that looks like broken firmware:

CircuitPython auto-reloads after *every* file that lands. Finder copies
alphabetically, so `code.py` arrives third and the board immediately runs a
half-installed core - `nus.py`, `recovery.py` and `updater.py` are not there
yet. Each reload also interrupts the copy. The board ends up red-blinking with
a partial filesystem, and if `boot.py` made it across, the next reset hands the
filesystem to the script and CIRCUITPY goes read-only over USB, so copying a
fix silently does nothing.

This script avoids all of that. Entering the REPL suspends auto-reload for as
long as the prompt is held, so the whole core lands as one quiet batch:

    1. open the serial console and press Ctrl-C   (auto-reload suspended)
    2. copy every file, support modules first     (nothing runs)
    3. press Ctrl-D                               (one clean start)
    4. read the console back to confirm it worked

    python3 tools/install.py
    python3 tools/install.py --no-boot   # leave boot.py off; keeps CIRCUITPY
                                         # writable over USB while testing

Requires the handset connected over USB with CIRCUITPY mounted. On a board
already running a core, hold the handset OFF the hook while plugging it in -
otherwise boot.py gives the filesystem to the script and there is nothing to
copy to. See docs/RECOVERY.md.
"""

import argparse
import glob
import os
import select
import shutil
import sys
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.join(os.path.dirname(HERE), "firmware")
VOLUME = "/Volumes/CIRCUITPY"

# Dependency order, not alphabetical. Support modules first so that whatever
# runs next has everything it imports; boot.py last because it is the one that
# takes the filesystem away from USB.
ORDER = ["updater.py", "nus.py", "recovery.py", "app.py", "code.py", "boot.py"]


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    return ports[0] if ports else None


class Console:
    """The CircuitPython serial console, in raw mode."""

    def __init__(self, port):
        self.fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, a)

    def read(self, seconds):
        out, end = b"", time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                out += os.read(self.fd, 4096)
        return out.decode("utf-8", "replace")

    def write(self, data):
        os.write(self.fd, data)

    def close(self):
        os.close(self.fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-boot", action="store_true",
                        help="skip boot.py, leaving CIRCUITPY writable over USB")
    parser.add_argument("--volume", default=VOLUME)
    args = parser.parse_args()

    if not os.path.isdir(args.volume):
        print("error: %s is not mounted." % args.volume)
        print("Plug in the handset. If it already runs a core, hold it OFF the "
              "hook while connecting.")
        return 1
    if not os.access(args.volume, os.W_OK):
        print("error: %s is read-only." % args.volume)
        print("The script owns the filesystem. Unplug, hold the handset OFF the "
              "hook, and plug it back in.")
        return 1

    port = find_port()
    if port is None:
        print("error: no CircuitPython serial port found under /dev/cu.usbmodem*")
        return 1
    print("Board:  %s" % args.volume)
    print("Console: %s" % port)

    files = [f for f in ORDER if not (args.no_boot and f == "boot.py")]
    missing = [f for f in files if not os.path.exists(os.path.join(FIRMWARE, f))]
    if missing:
        print("error: missing from %s: %s" % (FIRMWARE, ", ".join(missing)))
        return 1

    console = Console(port)
    try:
        # Ctrl-C: stop the running script and hold the REPL. Auto-reload stays
        # suspended for as long as we sit here, which is the whole point.
        console.write(b"\x03")
        console.read(1.0)
        print("\nAuto-reload suspended (REPL held). Copying:")

        for name in files:
            source = os.path.join(FIRMWARE, name)
            shutil.copyfile(source, os.path.join(args.volume, name))
            print("  %-14s %5d bytes" % (name, os.path.getsize(source)))

        os.system("sync")
        # Let the host finish flushing before starting the core. Without this
        # the board sees a late write, auto-reloads, and the verification below
        # reads an interrupted run rather than a clean one.
        time.sleep(2.5)

        print("\nStarting the core...")
        console.write(b"\x04")          # soft reboot, one clean start
        output = console.read(8.0)
        if "auto-reload" in output.lower() and "advertising" not in output:
            # A straggling write got in anyway. Wait out the reload it caused
            # and read the run that follows.
            output = console.read(10.0)
    finally:
        console.close()

    print("-" * 60)
    print(output.strip() or "(no output)")
    print("-" * 60)

    if "PANIC" in output:
        print("FAILED: the core is incomplete. See the panic message above.")
        return 1
    if "Traceback" in output:
        print("FAILED: the core raised. Traceback above; also /last_error.txt.")
        return 1
    if "advertising" in output or "Connected" in output:
        print("OK: the handset is running and advertising.")
        if args.no_boot:
            print("\nboot.py was NOT installed, so CIRCUITPY is still writable "
                  "over USB and over-the-air updates will be refused "
                  "('err readonly'). Re-run without --no-boot to finish.")
        else:
            print("\nboot.py is installed: CIRCUITPY is read-only over USB from "
                  "the next power-cycle on the hook. To get the writable drive "
                  "back, hold the handset OFF the hook while plugging in.")
        return 0

    print("UNCLEAR: no familiar output. Watch the console before deploying.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
