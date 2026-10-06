"""Windows scan-code input. No third-party modules or game-process modification."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import PureWindowsPath
import sys

from .playback import FocusChanged

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t
WM_HOTKEY = 0x0312
HOTKEYS = {
    1: (0x77, "toggle"),
    2: (0x78, "stop"),
    3: (0x79, "quit"),
    4: (0x74, "all"),
    5: (0x75, "melody"),
    6: (0x76, "accompaniment"),
    7: (0x2D, "section_previous"),
    8: (0x2E, "section_next"),
    9: (0x21, "song_previous"),
    10: (0x22, "song_next"),
}
SCAN_CODES = dict(
    zip(
        "QWERTYUASDFGHJZXCVBNM",
        (
            0x10,
            0x11,
            0x12,
            0x13,
            0x14,
            0x15,
            0x16,
            0x1E,
            0x1F,
            0x20,
            0x21,
            0x22,
            0x23,
            0x24,
            0x2C,
            0x2D,
            0x2E,
            0x2F,
            0x30,
            0x31,
            0x32,
        ),
    )
)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("data", INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),
    ]


def make_input(key, key_up=False):
    item = INPUT()
    item.type = 1  # INPUT_KEYBOARD
    item.data.ki = KEYBDINPUT(0, SCAN_CODES[key], 0x0008 | (0x0002 if key_up else 0), 0, 0)
    return item


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    executable: str
    title: str


class WindowsKeyboard:
    def __init__(self, processes=("YuanShen.exe", "GenshinImpact.exe"), title=None):
        if sys.platform != "win32":
            raise RuntimeError("实际发键仅支持 Windows；--check 和 --dry-run 可在其他系统使用。")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = ctypes.c_uint
        self.user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        self.user32.GetAsyncKeyState.restype = ctypes.c_int16
        self.user32.RegisterHotKey.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        self.user32.RegisterHotKey.restype = ctypes.c_int
        self.user32.UnregisterHotKey.argtypes = (ctypes.c_void_p, ctypes.c_int)
        self.user32.UnregisterHotKey.restype = ctypes.c_int
        self.user32.PeekMessageW.argtypes = (
            ctypes.POINTER(MSG),
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        self.user32.PeekMessageW.restype = ctypes.c_int
        self.user32.GetForegroundWindow.argtypes = ()
        self.user32.GetForegroundWindow.restype = ctypes.c_void_p
        self.user32.GetWindowThreadProcessId.argtypes = (ctypes.c_void_p, ctypes.POINTER(DWORD))
        self.user32.GetWindowThreadProcessId.restype = DWORD
        self.user32.GetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int)
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.IsWindow.argtypes = (ctypes.c_void_p,)
        self.user32.IsWindow.restype = ctypes.c_int
        self.user32.IsWindowVisible.argtypes = (ctypes.c_void_p,)
        self.user32.IsWindowVisible.restype = ctypes.c_int
        self.user32.PostMessageW.argtypes = (
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t
        )
        self.user32.PostMessageW.restype = ctypes.c_int
        self.kernel32.OpenProcess.argtypes = (DWORD, ctypes.c_int, DWORD)
        self.kernel32.OpenProcess.restype = ctypes.c_void_p
        self.kernel32.QueryFullProcessImageNameW.argtypes = (
            ctypes.c_void_p,
            DWORD,
            ctypes.c_wchar_p,
            ctypes.POINTER(DWORD),
        )
        self.kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        self.kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.kernel32.CloseHandle.restype = ctypes.c_int
        self.processes = {p.casefold() for p in processes}
        self.title_filter = title.casefold() if title else None
        self.locked_window = None
        self.allow_background = False
        self.background_pressed = set()
        self.pressed = set()
        self.registered_hotkeys = []
        self.timer = None

    def key_down(self, virtual_key):
        # Only the high bit is reliable for detecting a currently held key.
        return bool(self.user32.GetAsyncKeyState(virtual_key) & 0x8000)

    def hotkeys(self):
        # Queued events also catch a key that was pressed and released between ticks.
        result = set()
        message = MSG()
        while self.user32.PeekMessageW(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1):
            if message.message == WM_HOTKEY and message.wParam in HOTKEYS:
                result.add(HOTKEYS[message.wParam][1])
            elif message.message == 0x0012:  # WM_QUIT is returned even with a filter.
                result.add("quit")
        return result

    def register_hotkeys(self):
        try:
            for ident, (vk, _) in HOTKEYS.items():
                if not self.user32.RegisterHotKey(None, ident, 0x4000, vk):  # MOD_NOREPEAT
                    raise OSError(
                        ctypes.get_last_error(),
                        "无法注册 {} 热键；请关闭另一个演奏器，或释放占用该热键的软件。".format(
                            {0x21: "PageUp", 0x22: "PageDown", 0x2D: "Insert", 0x2E: "Delete"}.get(
                                vk, "F{}".format(vk - 0x6F)
                            )
                        ),
                    )
                self.registered_hotkeys.append(ident)
        except BaseException:
            self.unregister_hotkeys()
            raise

    def unregister_hotkeys(self):
        for ident in self.registered_hotkeys:
            self.user32.UnregisterHotKey(None, ident)
        self.registered_hotkeys.clear()

    def modifiers_down(self):
        return any(self.key_down(vk) for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def foreground(self):
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return Window(0, 0, "", "")
        return self.window_info(hwnd)

    def window_info(self, hwnd):
        pid = DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(512)
        self.user32.GetWindowTextW(hwnd, title, len(title))
        name = ""
        process = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = DWORD(len(buf))
                if self.kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
                    name = PureWindowsPath(buf.value).name
            finally:
                self.kernel32.CloseHandle(process)
        return Window(int(hwnd), pid.value, name, title.value)

    def find_target(self):
        if self.locked_window:
            hwnd, pid = self.locked_window
            if self.user32.IsWindow(hwnd):
                window = self.window_info(hwnd)
                if window.pid == pid and self.matches(window):
                    return window
        found = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t)

        @callback_type
        def inspect(hwnd, unused):
            if self.user32.IsWindowVisible(hwnd):
                window = self.window_info(hwnd)
                if self.matches(window):
                    found.append(window)
                    return 0
            return 1

        self.user32.EnumWindows(inspect, 0)
        return found[0] if found else Window(0, 0, "", "")

    def matches(self, window):
        if not window.hwnd:
            return False
        if self.title_filter is not None:
            return self.title_filter in window.title.casefold()
        return window.executable.casefold() in self.processes

    def arm(self):
        window = self.find_target() if self.allow_background else self.foreground()
        if self.matches(window) and (self.allow_background or not self.modifiers_down()):
            self.locked_window = (window.hwnd, window.pid)
            return True
        return False

    def target_ready(self):
        if self.allow_background and self.locked_window:
            hwnd, pid = self.locked_window
            if not self.user32.IsWindow(hwnd):
                return False
            current_pid = DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(current_pid))
            if current_pid.value != pid:
                return False
            return self.user32.GetForegroundWindow() != hwnd or not self.modifiers_down()
        window = self.foreground()
        return (
            self.locked_window == (window.hwnd, window.pid)
            and self.matches(window)
            and not self.modifiers_down()
        )

    def _send(self, keys, key_up=False):
        if not keys:
            return
        data = (INPUT * len(keys))(*(make_input(k, key_up) for k in keys))
        sent = self.user32.SendInput(len(data), data, ctypes.sizeof(INPUT))
        if sent != len(data):
            error = ctypes.get_last_error()
            raise OSError(
                error,
                "Windows 仅接受了 {}/{} 个按键事件。若游戏以管理员身份运行，请以相同权限启动演奏器。".format(
                    sent, len(data)
                ),
            )

    def _post(self, keys, key_up=False):
        hwnd = self.locked_window[0]
        for key in keys:
            flags = 1 | (SCAN_CODES[key] << 16) | (0xC0000000 if key_up else 0)
            if not self.user32.PostMessageW(hwnd, 0x0101 if key_up else 0x0100,
                                            ord(key), flags):
                raise OSError(ctypes.get_last_error(), "后台按键未能发送到游戏窗口。")

    def press(self, keys):
        if not self.target_ready():
            raise FocusChanged("Foreground changed")
        self.pressed.update(keys)  # Track even partial SendInput failures for cleanup.
        if getattr(self, "allow_background", False) and self.user32.GetForegroundWindow() != self.locked_window[0]:
            self.background_pressed.update(keys)
            self._post(keys)
        else:
            self._send(keys)

    def release_all(self):
        if self.pressed:
            background_pressed = getattr(self, "background_pressed", set())
            posted = tuple(sorted(background_pressed))
            direct = tuple(sorted(self.pressed - background_pressed))
            if posted:
                self._post(posted, True)
            if direct:
                if (getattr(self, "allow_background", False) and self.locked_window
                        and self.user32.GetForegroundWindow() != self.locked_window[0]):
                    self._post(direct, True)
                else:
                    self._send(direct, True)
            background_pressed.clear()
            self.pressed.clear()

    def __enter__(self):
        self.register_hotkeys()
        try:
            timer = ctypes.WinDLL("winmm")
            timer.timeBeginPeriod.argtypes = (ctypes.c_uint,)
            timer.timeBeginPeriod.restype = ctypes.c_uint
            timer.timeEndPeriod.argtypes = (ctypes.c_uint,)
            timer.timeEndPeriod.restype = ctypes.c_uint
            if timer.timeBeginPeriod(1) == 0:
                self.timer = timer
        except BaseException:
            self.unregister_hotkeys()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.release_all()
        finally:
            self.unregister_hotkeys()
            if self.timer:
                self.timer.timeEndPeriod(1)
