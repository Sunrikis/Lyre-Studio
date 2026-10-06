"""Run with Python 3.8+: python player.py [songs/example.json]."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import time
import json

from .scores import ScoreError, load_score, prepare_score, choose_tap_seconds
from .playback import Playback
from .library import Song
from .settings import song_directory, initialize_library

ROOT = Path(__file__).resolve().parent
SONGS = song_directory()


def make_parser():
    p = argparse.ArgumentParser(
        description="21键通用演奏器：一首曲子一个 JSON 文件。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("score", nargs="?", help="谱文件路径；不填则显示曲目菜单")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--list", action="store_true", help="列出 songs 文件夹中的曲谱")
    modes.add_argument("--check", action="store_true", help="检查谱文件，不发送键盘输入")
    modes.add_argument(
        "--dry-run", action="store_true", help="输出完整按键时间表，不等待也不发送输入"
    )
    modes.add_argument("--foreground", action="store_true", help="只显示当前前台窗口信息")
    speed = p.add_mutually_exclusive_group()
    speed.add_argument("--speed", type=float, default=1.0, help="速度倍率，如 0.75 表示慢练")
    speed.add_argument("--bpm", type=float, help="指定演奏 BPM")
    p.add_argument("--countdown", type=float, default=3.0, help="F8 开始或继续后的倒计时秒数")
    p.add_argument(
        "--tap-ms", type=float, default=50.0, help="每次短按的毫秒数；密集音符会自动缩短"
    )
    p.add_argument(
        "--process",
        action="append",
        help="允许的游戏进程名，可重复指定；默认 YuanShen.exe / GenshinImpact.exe",
    )
    p.add_argument("--window-title", help="改为匹配指定窗口标题；仅在默认进程名不适用时使用")
    return p


def available_scores():
    return sorted(SONGS.glob("*.json"), key=lambda p: p.name.casefold())


def list_scores(files):
    print("可用曲谱：")
    for i, path in enumerate(files, 1):
        try:
            score = load_score(path)
            label = "{} · {:g} BPM · {} 次按键".format(score.title, score.bpm, len(score.events))
        except ScoreError as exc:
            label = "格式有误：{}".format(exc)
        print("  {}. {}\n     {}".format(i, label, path.name))


def choose_file(name):
    if name:
        candidate = Path(name)
        if candidate.is_file():
            return candidate
        candidate = SONGS / name
        if candidate.is_file():
            return candidate
        raise ScoreError("找不到曲谱：{}".format(name))
    files = available_scores()
    if not files:
        raise ScoreError("songs 文件夹中还没有 JSON 曲谱。")
    list_scores(files)
    while True:
        value = input("输入曲目编号（直接回车选 1，q 退出）：").strip()
        if value.lower() == "q":
            return None
        if not value:
            value = "1"
        if value.isdecimal() and 1 <= int(value) <= len(files):
            return files[int(value) - 1]
        print("请输入列表中的编号。")


def summary(score, tap):
    print("\n曲目：{}".format(score.title))
    print(
        "速度：{:g} BPM｜时长：{:.2f} 秒｜{} 组按键 / {} 个音｜最大和弦：{} 键".format(
            score.bpm,
            score.duration,
            len(score.events),
            score.note_count,
            max(len(e.keys) for e in score.events),
        )
    )
    print("短按时长：{:.1f} 毫秒".format(tap * 1000))


def run_live(score, args, song):
    # Import is deliberately below check/dry-run handling; no Win32 input in those modes.
    from .win_input import WindowsKeyboard

    processes = args.process or ("YuanShen.exe", "GenshinImpact.exe")
    with WindowsKeyboard(processes, args.window_title) as keyboard:
        player = Playback(score, keyboard, args.countdown, args.tap_ms)
        selected_part = "all"
        player.event_filter = (
            lambda index, event: event.keys
            if selected_part == "all"
            else song.events[index][selected_part]
        )
        print("\n请切换到游戏的乐器界面。")
        print("F8 开始 / 暂停 / 继续    F9 停止归零    F10 退出")
        print("F5 完整演奏    F6 只弹旋律    F7 只弹伴奏（实时切换，不暂停）")
        print("若游戏以管理员身份运行，请使用‘启动演奏台（管理员）.cmd’。")
        print("倒计时 {:g} 秒；切出游戏会暂停，切回来后须按 F8 继续。".format(args.countdown))
        print("请松开 Shift / Ctrl / Alt / Win；分声部跟练时请弹奏另一个声部。")
        last_status = None
        last_progress = -math.inf
        try:
            while True:
                now = time.perf_counter()
                commands = keyboard.hotkeys()
                if "quit" in commands:
                    break
                if "stop" in commands:
                    player.stop()
                elif "toggle" in commands:
                    ready = True if player.state in ("playing", "countdown") else keyboard.arm()
                    player.toggle(now, ready)
                elif commands.intersection({"all", "melody", "accompaniment"}):
                    part = next(p for p in ("all", "melody", "accompaniment") if p in commands)
                    if part == "all" or (song.has_parts and song.parts[part].events):
                        selected_part = part
                        print(
                            "\n当前声部："
                            + {
                                "all": "完整演奏",
                                "melody": "只弹旋律",
                                "accompaniment": "只弹伴奏",
                            }[part]
                        )
                    else:
                        print("\n此曲没有可用的所选声部。")
                player.tick(now, keyboard.target_ready())
                status = (player.state, player.reason)
                if status != last_status:
                    print("\n{}".format(player.reason), flush=True)
                    last_status = status
                if player.state in ("countdown", "playing") and now - last_progress >= 0.25:
                    if player.state == "countdown":
                        message = "即将开始：{:.1f} 秒".format(max(0, player.countdown_end - now))
                    else:
                        message = "进度 {:6.1f} / {:6.1f} 秒    {:4d} / {:4d} 组".format(
                            player.elapsed(now), score.duration, player.index, len(score.events)
                        )
                    print("\r{:<72}".format(message), end="", flush=True)
                    last_progress = now
                # Scheduling uses absolute perf_counter deadlines, not chained note sleeps.
                time.sleep(0.002 if player.state in ("playing", "countdown") else 0.01)
        finally:
            player.close()
            print("\n演奏器已停止，已释放程序按下的琴键。")


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if not math.isfinite(args.countdown) or not 0 <= args.countdown <= 30:
            raise ScoreError("倒计时应为 0～30 秒。")
        if args.window_title is not None and not args.window_title.strip():
            raise ScoreError("窗口标题不可为空。")
        if args.list:
            initialize_library(SONGS)
            list_scores(available_scores())
            return 0
        if args.foreground:
            from .win_input import WindowsKeyboard

            keyboard = WindowsKeyboard(
                args.process or ("YuanShen.exe", "GenshinImpact.exe"), args.window_title
            )
            window = keyboard.foreground()
            print(
                "前台窗口：{}\n进程：{}\nPID：{}\n符合目标：{}".format(
                    window.title, window.executable, window.pid, keyboard.matches(window)
                )
            )
            return 0
        if not args.score:
            initialize_library(SONGS)
        path = choose_file(args.score)
        if path is None:
            return 0
        score = load_score(path)
        speed = args.bpm / score.bpm if args.bpm is not None else args.speed
        score = prepare_score(score, speed)
        tap = choose_tap_seconds(score, args.tap_ms)
        summary(score, tap)
        if args.check:
            print("检查通过。未发送任何键盘输入。")
        elif args.dry_run:
            print("\n时间表（不发送键盘输入）：")
            for event in score.events:
                print("{:9.3f} 秒  [{}]".format(event.time, "".join(event.keys)))
        else:
            song = Song(path.name, json.loads(path.read_text(encoding="utf-8-sig")))
            run_live(score, args, song)
        return 0
    except (ScoreError, OSError, RuntimeError) as exc:
        print("\n无法继续：{}".format(exc), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n已退出。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
