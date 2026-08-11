# Tonematic Pro - boot configuration. Part of the immutable core.
#
# CircuitPython cannot let the script and the USB host write CIRCUITPY at the
# same time, so one of them has to give it up. Over-the-air updates need the
# script to win, but permanently surrendering USB write access would mean a
# board with broken firmware could only be recovered by reflashing the runtime.
#
# The cradle microswitch resolves it. It is the only input the handset has, and
# at boot nothing else is using it:
#
#   powered up ON the hook  (normal service)  -> script writes, USB read-only
#   powered up OFF the hook (recovery)        -> USB writes, stock behaviour
#
# So to get a drag-and-drop CIRCUITPY back, lift the handset out of the cradle
# and hold it there while plugging in USB. That is the documented recovery
# gesture - see docs/RECOVERY.md.
#
# Deliberately minimal and dependency-free: an exception here would leave the
# filesystem in whichever state it was already in, so there is nothing to gain
# from doing more work in this file.

import board
import digitalio
import storage

hook = digitalio.DigitalInOut(board.D13)
hook.direction = digitalio.Direction.INPUT
hook.pull = digitalio.Pull.UP
# Pulled up, so the switch reads False when the handset is off the cradle.
off_hook = not hook.value
# Release the pin so code.py can claim it.
hook.deinit()

if not off_hook:
    storage.remount("/", readonly=False)
