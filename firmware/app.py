# Copyright (C) 2024-2026 We Fly Kites Pty Ltd (www.breezesys.com).
# Licensed under the Breeze Booth Tonematic Pro Firmware License - see LICENSE.
#
# Tonematic Pro - the booth payload. This is the file that ships over the air;
# everything else on the board is the immutable core (see code.py).
#
# Entry point is run(env), called by code.py. env is a dict - read it with
# .get() so a payload keeps working on an older core.

import time
import board
import digitalio
import neopixel
from adafruit_led_animation.color import RED, GREEN, BLUE, ORANGE

import updater
import nus

APP_VERSION = "0.3"

debug = True


def outputdebug(s):
    if debug:
        print(s)


def run(env):
    statusLED = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.5,
                                  auto_write=True, pixel_order=neopixel.GRB)

    # digital I/O pin to read the microswitch connected to the cradle
    handset = digitalio.DigitalInOut(board.D13)
    handset.direction = digitalio.Direction.INPUT
    handset.pull = digitalio.Pull.UP

    up = updater.Updater(app_version=APP_VERSION,
                         status=env.get("status", "ok"))
    ble, uart, advertisement = nus.start()

    prevOffHook = handset.value
    while True:
        outputdebug("Not connected, advertising service: " + ble.name)
        while not ble.connected:
            offHook = not handset.value
            if offHook:
                statusLED[0] = ORANGE
            else:
                statusLED[0] = RED
            time.sleep(0.1)
            if prevOffHook != offHook:
                prevOffHook = offHook
                outputdebug("off hook" if offHook else "on hook")

        outputdebug("Connected...")
        statusLED[0] = GREEN
        state = ""
        allowStart = False
        # Report the firmware version unprompted, so the booth can decide
        # whether to push an update without having to ask first.
        up.handle("u:?", uart)

        while ble.connected:
            if uart.in_waiting:
                s = nus.readline(uart)
                if not s:
                    continue
                if up.handle(s, uart):
                    # A firmware line. Nothing else to do with it.
                    continue
                if s[0:2] == "s:":
                    state = s
                    outputdebug(s)
            elif up.busy:
                # Mid-transfer. Ignore the cradle entirely - a lift now would
                # start a session the guest is not there for, and the booth is
                # busy writing firmware.
                time.sleep(0.01)
            else:
                offHook = not handset.value
                if offHook:
                    statusLED[0] = BLUE
                else:
                    statusLED[0] = GREEN
                if prevOffHook != offHook:
                    prevOffHook = offHook
                    # A completed hook cycle is the physical-presence proof for
                    # updater.REQUIRE_PHYSICAL_ARM. No-op when that is off.
                    up.arm()

                # "standby" and "powerSaving" are the booth's two idle screens:
                # standby after a period of inactivity, powerSaving on low
                # battery. Both pause the live preview, but lifting the handset
                # must still start a session, so both are treated exactly like
                # videoReady - the booth wakes itself when it receives
                # videoStart.
                #
                # Without these nothing matches while the booth is idle, so the
                # lift is read into offHook and then discarded, and allowStart
                # is never re-armed either (it is only set inside this branch).
                # The handset goes completely dead until someone touches the
                # booth screen.
                if "videoReady" in state or ": standby" in state or ": powerSaving" in state:
                    if offHook and allowStart:
                        allowStart = False
                        outputdebug("Start countdown")
                        uart.write("videoStart\n")
                        time.sleep(0.1)
                    elif not offHook and not allowStart:
                        allowStart = True
                        outputdebug("allow start in video ready/standby/power saving when handset is on hook")
                        time.sleep(0.1)
                elif "videoCountdown" in state:
                    if not offHook:
                        # user replaced handset during countdown: cancel
                        uart.write("cancelCountdown\n")
                        outputdebug("User replaced handset during countdown: cancel")
                        time.sleep(0.1)
                elif "videoCapture" in state:
                    if not offHook:
                        # user replaced handset during capture: end the video
                        outputdebug("User replaced handset during capture: end capture")
                        uart.write("videoEnd\n")
                        time.sleep(0.1)
                elif ": gallery" in state:
                    # disable start in gallery so that we can lift the handset
                    # to listen to captures
                    if allowStart:
                        allowStart = False
                        outputdebug("Disable start in gallery")

        outputdebug("Disconnected, re-advertising")
        ble.start_advertising(advertisement)
