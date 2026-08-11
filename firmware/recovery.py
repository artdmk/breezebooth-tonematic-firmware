# Tonematic Pro - recovery mode. Part of the immutable core.
#
# Reached when app.py could not run and there is no previous payload to fall
# back to. It does nothing useful for a guest - no hook switch, no booth
# commands - but it advertises under the normal name and still serves the
# updater, so the unit can be repaired over the air instead of in person.
#
# That is the whole point: a handset in recovery is broken but reachable.
#
# The LED is the on-site tell. Recovery alternates purple/white, which none of
# the normal states use, so a tech can identify a unit needing attention
# without a laptop.

import time

import updater
import nus

PURPLE = (255, 0, 255)
WHITE = (255, 255, 255)


def _led():
    """The NeoPixel, or None. Recovery has to work on a board whose LED or
    library is part of the problem."""
    try:
        import board
        import neopixel
        return neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.5,
                                 auto_write=True, pixel_order=neopixel.GRB)
    except Exception:
        return None


def run(env, failure=None):
    print("RECOVERY MODE:", failure)
    led = _led()
    up = updater.Updater(app_version="none", status="recovery")

    ble, uart, advertisement = nus.start()
    print("Recovery advertising:", ble.name)

    blink = False
    while True:
        while not ble.connected:
            if led:
                led[0] = PURPLE if blink else WHITE
            blink = not blink
            time.sleep(0.25)

        if led:
            led[0] = WHITE
        # Announce unprompted so the booth learns this unit needs firmware
        # without having to poll for it.
        up.handle("u:?", uart)

        while ble.connected:
            if uart.in_waiting:
                line = nus.readline(uart)
                if line:
                    up.handle(line, uart)
            else:
                time.sleep(0.02)

        print("Recovery: disconnected, re-advertising")
        ble.start_advertising(advertisement)
