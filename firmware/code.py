# Tonematic Pro - launcher. Part of the immutable core.
#
# This file is deliberately tiny and is NOT shipped over the air. It exists so
# that a bad over-the-air payload cannot brick a handset.
#
# Without it, a payload that raises would leave CircuitPython sitting in the
# REPL: not advertising, not reachable, dead until someone drives to the venue.
# So the booth logic lives in app.py, which this file runs inside a try/except,
# and there are three ways back from a bad payload:
#
#   1. app.py raises          -> restore app_prev.py and reset
#   2. app.py runs but the booth never confirms the update, three boots running
#                             -> restore app_prev.py and reset
#   3. no previous payload    -> recovery.py, a minimal BLE loop that still
#                                serves the updater so the unit stays reachable
#
# Case 2 matters as much as case 1: a payload that starts cleanly but cannot
# talk to the booth (wrong advertised name, a hang in the connect loop) would
# otherwise be just as unreachable as one that crashed.
#
# EVERY import below is guarded. This file must survive being the only part of
# the core present on the board - which is exactly what happens while somebody
# is copying the core over USB, because CircuitPython reloads after each file
# lands and will run a half-installed core. An unguarded `import updater` here
# turns a routine mid-copy reload into a red-blinking board. See
# tools/install.py, which suspends auto-reload for the duration of a copy.
#
# The code.py <-> app.py contract is frozen: app.run(env) with a dict, so the
# core can add keys later without breaking payloads written against an older
# one. Payloads must read it with env.get().

import time

# Boots a staged payload gets to prove itself before it is rolled back.
MAX_UNCONFIRMED_BOOTS = 3


def panic(message):
    """Last resort: the core itself is incomplete or broken.

    Blinks red slowly and forever rather than dropping to the REPL, so a unit
    in this state is obvious on a shelf. Auto-reload is deliberately left on
    everywhere in this file, so copying the missing file over USB still fixes
    a board that has got here.
    """
    print("PANIC:", message)
    try:
        import board
        import neopixel
        led = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.5,
                                auto_write=True, pixel_order=neopixel.GRB)
    except Exception:
        led = None
    while True:
        if led:
            led[0] = (255, 0, 0)
        time.sleep(0.5)
        if led:
            led[0] = (0, 0, 0)
        time.sleep(0.5)


try:
    import updater
except Exception as e:
    panic("updater.py missing or broken: %s" % e)

try:
    import microcontroller
except Exception as e:
    panic("microcontroller unavailable: %s" % e)

status = "ok"
pending = updater.read_pending()
if pending is not None:
    crc_hex, boots = pending
    if boots >= MAX_UNCONFIRMED_BOOTS:
        print("Payload unconfirmed after %d boots, rolling back" % boots)
        if updater.rollback():
            microcontroller.reset()
        status = "ok"
    else:
        status = "pending"
        try:
            updater.write_pending(crc_hex, boots + 1)
        except OSError:
            pass

env = {
    "core": updater.CORE_VERSION,
    "status": status,
    "writable": updater.writable(),
}

failure = None
try:
    import app
    app.run(env)
    # run() owns the main loop and is not expected to return. If it does,
    # treat it as a failure so the unit does not sit idle and unreachable.
    failure = "app.run() returned"
except Exception as e:  # noqa: BLE001 - anything at all must be survivable
    failure = e

print("Payload failed:", failure)
try:
    import traceback
    with open("/last_error.txt", "w") as f:
        if isinstance(failure, Exception):
            traceback.print_exception(failure, file=f)
        else:
            f.write(str(failure) + "\n")
except Exception:
    pass

if status == "pending" and updater.rollback():
    print("Rolled back to previous payload")
    microcontroller.reset()

try:
    import recovery
except Exception as e:
    panic("app.py failed and recovery.py is missing: %s" % e)

recovery.run(env, failure)
