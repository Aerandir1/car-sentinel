"""pyserial : serial_for_url("androidusb://") passe par la clé USB vue par Android (UsbSerial.java)."""

import time

from java import jclass
from serial.serialutil import PortNotOpenError, SerialBase, SerialException, SerialTimeoutException

Usb = jclass("fr.car.sentinel.UsbSerial")


def _msg(e):
    """Message d'une IOException Java (sans « java.io.IOException: »)."""
    s = str(e)
    return s.split(": ", 1)[1] if s.startswith("java.") and ": " in s else s


def _ms(seconds, default):
    return default if seconds is None else max(0, int(seconds * 1000))


class Serial(SerialBase):

    def open(self):
        if self.is_open:
            raise SerialException("port déjà ouvert")
        try:
            Usb.open(self._baudrate or 0)
        except Exception as e:   # IOException Java : message en français écrit par UsbSerial
            raise SerialException(_msg(e)) from None
        self.is_open = True

    def close(self):
        if self.is_open:
            Usb.close()
            self.is_open = False

    def _reconfigure_port(self, force_update=False):
        if self.is_open:
            Usb.setBaudRate(self._baudrate or 0)

    @property
    def in_waiting(self):
        if not self.is_open:
            raise PortNotOpenError()
        return Usb.available()

    def read(self, size=1):
        if not self.is_open:
            raise PortNotOpenError()
        out = bytearray()
        end = None if self._timeout is None else time.monotonic() + self._timeout
        while len(out) < size:
            wait = 200 if end is None else max(0, int((end - time.monotonic()) * 1000))
            try:
                chunk = Usb.read(size - len(out), wait)
            except Exception as e:
                raise SerialException(_msg(e)) from None
            out += bytes(chunk)
            if end is not None and time.monotonic() >= end:
                break
        return bytes(out)

    def write(self, data):
        if not self.is_open:
            raise PortNotOpenError()
        data = bytes(data)
        try:
            Usb.write(data, _ms(self._write_timeout, 1000))
        except Exception as e:
            if "timeout" in str(e).lower():
                raise SerialTimeoutException("écriture trop longue") from None
            raise SerialException(_msg(e)) from None
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        if self.is_open:
            Usb.clearInput()

    def reset_output_buffer(self):
        pass
