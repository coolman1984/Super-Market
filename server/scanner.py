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

    def write(self, data):
        """A request character for scales that answer only when asked (never used for scanners)."""
        os.write(self.fd, data)

    def close(self):
        os.close(self.fd)


def win_structs():
    """DCB and COMMTIMEOUTS with explicit 32/16-bit fields (DWORD/WORD are
    32/16-bit on Windows on every architecture; never use C long here)."""
    import ctypes
    DWORD, WORD, BYTE, CHAR = ctypes.c_uint32, ctypes.c_uint16, ctypes.c_ubyte, ctypes.c_char

    class DCB(ctypes.Structure):
        _fields_ = [("DCBlength", DWORD), ("BaudRate", DWORD), ("flags", DWORD), ("wReserved", WORD),
                    ("XonLim", WORD), ("XoffLim", WORD), ("ByteSize", BYTE), ("Parity", BYTE), ("StopBits", BYTE),
                    ("XonChar", CHAR), ("XoffChar", CHAR), ("ErrorChar", CHAR), ("EofChar", CHAR), ("EvtChar", CHAR),
                    ("wReserved1", WORD)]

    class COMMTIMEOUTS(ctypes.Structure):
        _fields_ = [("ReadIntervalTimeout", DWORD), ("ReadTotalTimeoutMultiplier", DWORD),
                    ("ReadTotalTimeoutConstant", DWORD), ("WriteTotalTimeoutMultiplier", DWORD),
                    ("WriteTotalTimeoutConstant", DWORD)]
    return DCB, COMMTIMEOUTS


def dcb_flags(parity):
    """DCB bit field: fBinary (bit 0) always; fParity (bit 1) only when a
    parity is used; fDtrControl=ENABLE (bits 4-5 = 01); no flow control."""
    return 0x1 | (0x2 if parity != "none" else 0) | 0x10


class WindowsSerial:  # pragma: no cover - needs a Windows COM port (structures are unit tested)
    def __init__(self, path, baud=9600, bits=8, parity="none", stop=1):
        import ctypes
        from ctypes import wintypes
        if bits not in (7, 8) or parity not in PARITIES or stop not in (1, 2) or baud not in PosixSerial.BAUDS:
            raise ValueError("bad serial settings")
        self.ct = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        HANDLE, BOOL, LPVOID = wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p
        DCB, TIMEOUTS = win_structs()
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, ctypes.c_uint32, ctypes.c_uint32, LPVOID, ctypes.c_uint32,
                                    ctypes.c_uint32, HANDLE]
        k32.CreateFileW.restype = HANDLE
        k32.GetCommState.argtypes = [HANDLE, ctypes.POINTER(DCB)]
        k32.SetCommState.argtypes = [HANDLE, ctypes.POINTER(DCB)]
        k32.SetCommTimeouts.argtypes = [HANDLE, ctypes.POINTER(TIMEOUTS)]
        k32.ReadFile.argtypes = [HANDLE, LPVOID, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), LPVOID]
        k32.WriteFile.argtypes = [HANDLE, ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), LPVOID]
        k32.CloseHandle.argtypes = [HANDLE]
        for fn in ("GetCommState", "SetCommState", "SetCommTimeouts", "ReadFile", "WriteFile", "CloseHandle"):
            getattr(k32, fn).restype = BOOL
        self.k32 = k32
        name = path if path.startswith("\\\\.\\") else "\\\\.\\" + path
        handle = k32.CreateFileW(name, 0xC0000000, 0, None, 3, 0, None)  # GENERIC_READ|WRITE, OPEN_EXISTING
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            raise OSError("cannot open %s (error %d)" % (path, ctypes.get_last_error()))
        self.handle = handle
        try:
            dcb = DCB()
            dcb.DCBlength = ctypes.sizeof(DCB)
            if not k32.GetCommState(handle, ctypes.byref(dcb)):
                raise OSError("GetCommState failed (error %d)" % ctypes.get_last_error())
            dcb.BaudRate, dcb.ByteSize = baud, bits
            dcb.Parity = {"none": 0, "odd": 1, "even": 2}[parity]
            dcb.StopBits = 0 if stop == 1 else 2
            dcb.flags = dcb_flags(parity)
            if not k32.SetCommState(handle, ctypes.byref(dcb)):
                raise OSError("SetCommState failed (error %d)" % ctypes.get_last_error())
            # return at once with what arrived, or after 50 ms with nothing; a write (scale request) gives up after 200 ms
            if not k32.SetCommTimeouts(handle, ctypes.byref(TIMEOUTS(0xFFFFFFFF, 0xFFFFFFFF, 50, 0, 200))):
                raise OSError("SetCommTimeouts failed (error %d)" % ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def read(self, timeout=0.05):
        buf = self.ct.create_string_buffer(4096)
        n = self.ct.c_uint32()
        if not self.k32.ReadFile(self.handle, buf, 4096, self.ct.byref(n), None):
            raise OSError("ReadFile failed (error %d)" % self.ct.get_last_error())
        return buf.raw[:n.value]

    def write(self, data):
        n = self.ct.c_uint32()
        if not self.k32.WriteFile(self.handle, data, len(data), self.ct.byref(n), None):
            raise OSError("WriteFile failed (error %d)" % self.ct.get_last_error())

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

    def write(self, data):
        self.sock.sendall(data)

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
    """Serial/TCP scanner events for the tills.

    - Each device is bound to ONE workstation by the shop (settings); a till
      receives only its own devices' reads (R09).
    - Per workstation one window holds the lease; another window must claim it
      (the previous one is told so) - a read never lands in two carts.
    - `epoch` changes on every server start; a browser holding an old cursor
      is reset instead of waiting forever (R13). Reads that fell out of the
      bounded queue are COUNTED and reported, never silently lost.
    - Every frame has its own id: two identical scans are two events."""

    def __init__(self, size=500):
        import secrets
        self.events = collections.deque(maxlen=size)
        self.next_id = 1
        self.cond = threading.Condition()
        self.devices = {}
        self.bindings = {}
        self.leases = {}
        self.epoch = secrets.token_hex(6)

    def push(self, device_id, text):
        with self.cond:
            event = {"id": self.next_id, "device": device_id, "text": text, "seen": visible(text),
                     "at": time.time()}
            self.next_id += 1
            self.events.append(event)
            self.cond.notify_all()
            return event

    def since(self, after, wait=0.0, device=None):
        """Diagnostics view (scanner setup screen): reads everything, consumes nothing."""
        deadline = time.monotonic() + wait
        with self.cond:
            while True:
                found = [e for e in self.events if e["id"] > after and (device is None or e["device"] == device)]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found, self.next_id - 1
                self.cond.wait(remaining)

    def claim(self, workstation, window):
        with self.cond:
            self.leases[workstation] = window
            self.cond.notify_all()
            return window

    def take(self, workstation, window, after, wait=0.0, epoch=None):
        from .core import ShopError
        deadline = time.monotonic() + wait
        with self.cond:
            info = {"epoch": self.epoch, "restarted": False, "missed": 0}
            if epoch is not None and epoch != self.epoch:
                after, info["restarted"] = 0, True
            mine = {d for d, ws in self.bindings.items() if ws == workstation}
            while True:
                if self.leases.get(workstation) != window:
                    raise ShopError("scanner_taken_by_other_window", status=409)
                oldest = self.events[0]["id"] if self.events else self.next_id
                if after + 1 < oldest:
                    info["missed"] = sum(1 for _ in range(after + 1, oldest))   # dropped from the full queue
                found = [e for e in self.events if e["id"] > after and e["device"] in mine]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found, self.next_id - 1, info
                self.cond.wait(remaining)

    def configure(self, devices):
        for dev in self.devices.values():
            dev.stop.set()
        self.devices = {}
        self.bindings = {}
        for cfg in devices or []:
            if cfg.get("workstation"):
                self.bindings[cfg["id"]] = cfg["workstation"]
            if cfg.get("enabled", True) and cfg.get("type") in ("serial", "tcp"):
                if cfg.get("role") == "scale":
                    from .scale import ScaleReader
                    dev = ScaleReader(cfg, open_transport)
                else:
                    dev = Device(cfg, self.push)
                self.devices[cfg["id"]] = dev
                dev.thread.start()

    def weight(self, workstation):
        """The live weight of the scale bound to this till (None when it has no scale)."""
        for dev_id, dev in self.devices.items():
            if hasattr(dev, "reading") and self.bindings.get(dev_id) == workstation:
                return dev.reading()
        return None

    def status(self):
        out = []
        for d in self.devices.values():
            row = d.describe()
            row["workstation"] = self.bindings.get(row["id"])
            if not row["workstation"]:
                row["status"] = "needs_workstation"
            out.append(row)
        return out

    def stop(self):
        for dev in self.devices.values():
            dev.stop.set()
