"""Scanner input transports. Keyboard-wedge scanners (USB HID keyboard,
2.4 GHz dongles, Bluetooth HID) type into the browser and never reach this
module. This module serves scanners that send bytes over a real or virtual
COM port (RS-232, USB-CDC "virtual COM", Bluetooth SPP) or a TCP socket.

Transport (bytes) -> Framer (prefix/suffix/gap) -> event queue -> browser.
No vendor SDK, no automatic change of device settings. Standard library only:
termios on Linux/macOS, ctypes+kernel32 on Windows (Windows path is not yet
verified on hardware; see docs/SCANNER_COMPATIBILITY.md).
"""

import collections
import os
import socket
import sys
import threading
import time

from .barcode import MAX_SCAN, visible

TERMINATORS = {"CR": b"\r", "LF": b"\n", "CRLF": b"\r\n", "TAB": b"\t", "ETX": b"\x03", "EOT": b"\x04"}
PARITIES = ("none", "even", "odd")


class Framer:
    """Assemble scanner frames from arbitrary byte chunks.

    prefix: bytes stripped when present at frame start (STX etc).
    suffix: terminator name from TERMINATORS or None (then gap_ms decides).
    gap_ms: silence that ends a frame when there is no suffix.
    partial_ms: with a suffix, a partial frame older than this is discarded
    (counted in `dropped`) instead of being glued to the next scan.
    """

    def __init__(self, prefix=b"", suffix="CR", gap_ms=80, encoding="latin-1", max_len=MAX_SCAN, partial_ms=1500):
        if suffix is not None and suffix not in TERMINATORS:
            raise ValueError("unknown suffix")
        self.prefix = prefix or b""
        self.suffix = TERMINATORS.get(suffix) if suffix else None
        self.gap = gap_ms / 1000.0
        self.partial = partial_ms / 1000.0  # with a suffix: slow Bluetooth chunks still join
        self.encoding = encoding
        self.max_len = max_len
        self.buffer = b""
        self.last = None
        self.dropped = 0

    def feed(self, chunk, at=None):
        at = time.monotonic() if at is None else at
        frames = self.poll(at)
        self.buffer += chunk
        self.last = at
        if self.suffix:
            while self.suffix in self.buffer:
                frame, self.buffer = self.buffer.split(self.suffix, 1)
                self._emit(frame, frames)
            # CRLF devices configured as CR leave a leading LF: ignore it
            self.buffer = self.buffer.lstrip(b"\n") if self.suffix == b"\r" else self.buffer
        if len(self.buffer) > self.max_len:
            self.buffer = b""
            self.dropped += 1
        return frames

    def poll(self, at=None):
        """Time-based flush; call periodically when no data arrives."""
        at = time.monotonic() if at is None else at
        frames = []
        limit = self.partial if self.suffix else self.gap
        if self.buffer and self.last is not None and at - self.last >= limit:
            if self.suffix:
                self.dropped += 1       # partial frame never terminated
            else:
                self._emit(self.buffer, frames)
            self.buffer = b""
        return frames

    def _emit(self, frame, out):
        frame = frame.strip(b"\n")
        if self.prefix and frame.startswith(self.prefix):
            frame = frame[len(self.prefix):]
        if not frame:
            return
        out.append(frame.decode(self.encoding, errors="replace"))


# ---------------- transports ----------------

class PosixSerial:
    BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

    def __init__(self, path, baud=9600, bits=8, parity="none", stop=1):
        import termios
        import tty
        if baud not in self.BAUDS or bits not in (7, 8) or parity not in PARITIES or stop not in (1, 2):
            raise ValueError("bad serial settings")
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        speed = getattr(termios, "B%d" % baud)
        attrs[4] = attrs[5] = speed
        cflag = attrs[2] & ~(termios.CSIZE | termios.PARENB | termios.PARODD | termios.CSTOPB)
        cflag |= termios.CS8 if bits == 8 else termios.CS7
        if parity != "none":
            cflag |= termios.PARENB | (termios.PARODD if parity == "odd" else 0)
        if stop == 2:
            cflag |= termios.CSTOPB
        attrs[2] = cflag | termios.CLOCAL | termios.CREAD
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def read(self, timeout=0.05):
        import select
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return b""
        data = os.read(self.fd, 4096)
        if data == b"":
            raise OSError("port closed")
        return data

    def close(self):
        os.close(self.fd)


class WindowsSerial:  # pragma: no cover - exercised only on Windows hardware
    def __init__(self, path, baud=9600, bits=8, parity="none", stop=1):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32 = k32
        name = path if path.startswith("\\\\.\\") else "\\\\.\\" + path
        k32.CreateFileW.restype = wintypes.HANDLE
        handle = k32.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)  # GENERIC_READ|WRITE, OPEN_EXISTING
        if handle in (None, wintypes.HANDLE(-1).value):
            raise OSError("cannot open %s (error %d)" % (path, ctypes.get_last_error()))
        self.handle = handle

        class DCB(ctypes.Structure):
            _fields_ = [("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("wReserved", wintypes.WORD), ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
                        ("ByteSize", ctypes.c_ubyte), ("Parity", ctypes.c_ubyte), ("StopBits", ctypes.c_ubyte),
                        ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                        ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", wintypes.WORD)]

        class TIMEOUTS(ctypes.Structure):
            _fields_ = [("ReadIntervalTimeout", wintypes.DWORD), ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                        ("ReadTotalTimeoutConstant", wintypes.DWORD), ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                        ("WriteTotalTimeoutConstant", wintypes.DWORD)]
        dcb = DCB()
        dcb.DCBlength = ctypes.sizeof(DCB)
        if not k32.GetCommState(handle, ctypes.byref(dcb)):
            self.close()
            raise OSError("GetCommState failed")
        dcb.BaudRate, dcb.ByteSize = baud, bits
        dcb.Parity = {"none": 0, "odd": 1, "even": 2}[parity]
        dcb.StopBits = 0 if stop == 1 else 2
        dcb.flags = 0x1 | 0x10  # fBinary, fDtrControl=ENABLE
        if not k32.SetCommState(handle, ctypes.byref(dcb)):
            self.close()
            raise OSError("SetCommState failed")
        timeouts = TIMEOUTS(0xFFFFFFFF, 0xFFFFFFFF, 50, 0, 0)  # return after 50 ms with what arrived
        k32.SetCommTimeouts(handle, ctypes.byref(timeouts))

    def read(self, timeout=0.05):
        buf = self.ct.create_string_buffer(4096)
        n = self.wt.DWORD()
        if not self.k32.ReadFile(self.handle, buf, 4096, self.ct.byref(n), None):
            raise OSError("ReadFile failed (error %d)" % self.ct.get_last_error())
        return buf.raw[:n.value]

    def close(self):
        if getattr(self, "handle", None):
            self.k32.CloseHandle(self.handle)
            self.handle = None


class TcpSource:
    """Client connection to a scanner/base station that serves raw text on TCP."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, int(port)), timeout=3)
        self.sock.settimeout(0.05)

    def read(self, timeout=0.05):
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return b""
        if data == b"":
            raise OSError("connection closed")
        return data

    def close(self):
        self.sock.close()


def open_transport(cfg):
    if cfg["type"] == "serial":
        cls = WindowsSerial if sys.platform == "win32" else PosixSerial
        return cls(cfg["port"], int(cfg.get("baud", 9600)), int(cfg.get("bits", 8)), cfg.get("parity", "none"),
                   int(cfg.get("stop", 1)))
    if cfg["type"] == "tcp":
        return TcpSource(cfg["host"], cfg["port"])
    raise ValueError("unknown transport")


def list_serial_ports():
    """Names only; presence of a port does not mean a scanner is attached."""
    if sys.platform == "win32":  # pragma: no cover
        import winreg
        ports = []
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
            i = 0
            while True:
                try:
                    ports.append(winreg.EnumValue(key, i)[1])
                    i += 1
                except OSError:
                    break
        except OSError:
            pass
        return sorted(ports)
    import glob
    return sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*") + glob.glob("/dev/rfcomm*") +
                  glob.glob("/dev/tty.usb*") + glob.glob("/dev/cu.*"))


# ---------------- device manager ----------------

class Device:
    def __init__(self, cfg, sink):
        self.cfg = cfg
        self.sink = sink
        self.status = "starting"
        self.error = None
        self.stop = threading.Event()
        self.framer = Framer(cfg.get("prefix", "").encode("latin-1"), cfg.get("suffix", "CR"),
                             int(cfg.get("gap_ms", 80)), partial_ms=int(cfg.get("partial_ms", 1500)))
        self.thread = threading.Thread(target=self.run, name="scanner-%s" % cfg["id"], daemon=True)

    def run(self):
        backoff = 0.5
        while not self.stop.is_set():
            try:
                transport = open_transport(self.cfg)
            except (OSError, ValueError, KeyError) as exc:
                self.status, self.error = "disconnected", str(exc)[:200]
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 5)
                continue
            self.status, self.error, backoff = "connected", None, 0.5
            try:
                while not self.stop.is_set():
                    data = transport.read(0.05)
                    frames = self.framer.feed(data) if data else self.framer.poll()
                    for frame in frames:
                        self.sink(self.cfg["id"], frame)
            except OSError as exc:
                self.status, self.error = "disconnected", str(exc)[:200]
            finally:
                try:
                    transport.close()
                except OSError:
                    pass
                self.framer.buffer = b""

    def describe(self):
        return {"id": self.cfg["id"], "type": self.cfg["type"], "status": self.status, "error": self.error,
                "dropped_partial": self.framer.dropped}


class ScannerHub:
    """Holds configured serial/TCP devices and a bounded event queue that
    browsers long-poll. Every frame gets its own id: two identical scans are
    two events (two packs), a re-delivered event keeps its id."""

    def __init__(self, size=500):
        self.events = collections.deque(maxlen=size)
        self.next_id = 1
        self.cond = threading.Condition()
        self.devices = {}

    def push(self, device_id, text):
        with self.cond:
            event = {"id": self.next_id, "device": device_id, "text": text, "seen": visible(text),
                     "at": time.time()}
            self.next_id += 1
            self.events.append(event)
            self.cond.notify_all()
            return event

    def since(self, after, wait=0.0, device=None):
        deadline = time.monotonic() + wait
        with self.cond:
            while True:
                found = [e for e in self.events if e["id"] > after and (device is None or e["device"] == device)]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found, self.next_id - 1
                self.cond.wait(remaining)

    def configure(self, devices):
        for dev in self.devices.values():
            dev.stop.set()
        self.devices = {}
        for cfg in devices or []:
            if cfg.get("enabled", True) and cfg.get("type") in ("serial", "tcp"):
                dev = Device(cfg, self.push)
                self.devices[cfg["id"]] = dev
                dev.thread.start()

    def status(self):
        return [d.describe() for d in self.devices.values()]

    def stop(self):
        for dev in self.devices.values():
            dev.stop.set()
