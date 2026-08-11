# Tonematic Pro - Nordic UART bring-up. Part of the immutable core.
#
# Both the payload and recovery mode need the same radio, the same advertised
# name and the same buffer size, and getting any of them wrong is what makes a
# handset unreachable. Keeping them here means an over-the-air payload cannot
# change how the unit is discovered - only what it does once connected.
#
# The advertised name is "BreezeAudio_" + the radio address, so every unit is
# unique. The booth matches on the "Breeze" prefix plus the UART service UUID,
# so the prefix is load-bearing: change it and the booth stops seeing handsets.

import binascii
from adafruit_ble import BLERadio
from adafruit_ble.advertising.standard import ProvideServicesAdvertisement
from adafruit_ble.services.nordic import UARTService

NAME_PREFIX = "BreezeAudio_"

# The receive buffer is 64 bytes and cannot be changed. `buffer_size` is a
# parameter of the StreamIn/StreamOut characteristics *inside* UARTService,
# fixed where the class is defined - passing it to the constructor raises
# TypeError. Enlarging it would mean redefining the whole service.
#
# So the buffer is a hard constraint, and updater.MAX_DATA is sized to fit a
# whole protocol line inside it. Keep the two in step.
RX_BUFFER_SIZE = 64


def start():
    """Returns (ble, uart, advertisement) with advertising already running."""
    ble = BLERadio()
    ble.name = NAME_PREFIX + binascii.hexlify(ble.address_bytes).decode()
    uart = UARTService()
    advertisement = ProvideServicesAdvertisement(uart)
    ble.start_advertising(advertisement)
    return ble, uart, advertisement


def readline(uart):
    """One decoded, stripped line, or None if it was not valid UTF-8."""
    try:
        return uart.readline().decode("UTF-8").strip()
    except Exception:
        return None
