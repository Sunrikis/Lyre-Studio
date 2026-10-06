"""Launch a background server and open the browser with ordinary user permissions."""

import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser

from .settings import song_directory, source_root


def main(argv=None):
    parser = argparse.ArgumentParser(description="21键演奏台启动器")
    parser.add_argument("--admin", action="store_true", help="请求 Windows 管理员权限")
    parser.add_argument("--demo", action="store_true", help="仅网页试听，不创建游戏按键连接")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--songs", help="曲库目录，默认源码仓库 songs 或用户目录")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    directory = song_directory(args.songs)
    server_args = ["--port", str(args.port), "--songs", str(directory), "--no-browser"]
    if args.demo:
        server_args.append("--demo")
    if args.serve:
        from .web_app import main as serve

        return serve(server_args)
    if args.admin and sys.platform != "win32":
        parser.error("--admin 仅支持 Windows；其他系统可以 --demo 试听。")
    url = "http://127.0.0.1:{}/".format(args.port)
    opener = build_opener(ProxyHandler({}))

    def running():
        try:
            with opener.open(url + "api/status", timeout=0.5) as response:
                state = json.load(response)
            return state if state.get("application") == "lyre-studio-v1" else None
        except (OSError, ValueError):
            return None

    state = running()
    if state:
        if not args.no_browser:
            webbrowser.open(url)
        if args.admin and not state.get("administrator"):
            ctypes.windll.user32.MessageBoxW(
                None,
                "已有普通权限演奏台正在运行。请先在网页退出，再使用管理员入口启动。",
                "21键演奏台",
                0,
            )
        return 0
    executable = Path(sys.executable)
    if sys.platform == "win32" and executable.with_name("pythonw.exe").is_file():
        executable = executable.with_name("pythonw.exe")
    root = source_root()
    target = [str(root / "run.py"), "--serve"] if root else ["-m", "lyre_studio.web_app"]
    parameters = ["-B", "-X", "utf8"] + target + server_args
    if args.admin and not ctypes.windll.shell32.IsUserAnAdmin():
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(
            None,
            "runas",
            str(executable),
            subprocess.list2cmdline(parameters),
            str(root) if root else None,
            0,
        )
        if not result or result <= 32:
            print("管理员启动未完成。")
            return 1
    else:
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        subprocess.Popen([str(executable)] + parameters, cwd=root, **options)
    for unused in range(100):
        if running():
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        time.sleep(0.15)
    print("演奏台启动未完成。请关闭其他演奏器，或运行 python run.py --serve 查看原因。")
    return 1
