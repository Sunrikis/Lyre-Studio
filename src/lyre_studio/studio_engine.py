"""One transport for web controls and native hotkeys; all input stays on one thread."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import queue
import threading
import time

from .playback import Playback
from .scores import ScoreError, number, prepare_score, choose_tap_seconds


class PreviewKeyboard:
    def arm(self):
        return True

    def target_ready(self):
        return True

    def press(self, keys):
        pass

    def release_all(self):
        pass


class OutputKeyboard:
    def __init__(self, real=None):
        self.real = real
        self.output = "preview"
        self.preview = PreviewKeyboard()

    def current(self):
        if self.output == "game":
            if self.real is None:
                raise ScoreError("游戏按键尚未连接，请用管理员启动入口打开演奏台。")
            return self.real
        return self.preview

    def arm(self):
        return self.current().arm()

    def target_ready(self):
        return self.current().target_ready()

    def press(self, keys):
        self.current().press(keys)

    def release_all(self):
        if self.real is not None:
            self.real.release_all()


class Controller:
    def __init__(self, keyboard, game_available=False):
        self.keyboard = keyboard
        self.game_available = game_available
        self.song = self.player = None
        self.speed, self.part, self.output = 1.0, "all", "game" if game_available else "preview"
        self.keyboard.output = self.output
        self.countdown = 0.0
        self.background = True
        if self.keyboard.real is not None:
            self.keyboard.real.allow_background = True
        self.pending_until = None
        self.loop = dict(enabled=False, start=0.0, end=0.0)
        self.loop_count = 0
        self.revision = 0
        self.closed = False

    def position(self, now):
        return (
            0.0
            if self.player is None
            else min(self.song.score.duration, self.player.elapsed(now) * self.speed)
        )

    def _put(self, seconds, reason="已定位，准备开始"):
        p = self.player
        p.close()
        p.position = seconds / self.speed
        p.index = bisect_left([e.time for e in p.score.events], p.position - 1e-8)
        p.state, p.reason = "paused", reason
        p.releasing_at = None

    def select(self, song, now):
        was_playing = self.player is not None and self.player.state == "playing"
        deadline = self.pending_until
        part = self.part if song.has_parts and song.parts[self.part].events else "all"
        prepared = prepare_score(song.score, self.speed)
        replacement = Playback(prepared, self.keyboard, countdown=0)
        if self.player:
            self.player.close()
        self.pending_until = deadline
        self.song = song
        self.part = part
        self.loop = dict(enabled=False, start=0.0, end=song.score.duration)
        self.loop_count = 0
        self.player = replacement
        self.player.event_filter = self.event_keys
        if was_playing:
            self.player.state = "playing"
            self.player.origin = now
            self.player.reason = "已切歌 · 演奏中"
        else:
            self.player.reason = "已切歌 · 等待开始" if deadline is not None else "曲目已就绪"
        self.revision += 1

    def pause(self, now, reason="已暂停，可继续演奏"):
        self.pending_until = None
        if self.player:
            self.player.pause(now, reason)
        self.revision += 1

    def stop(self, now):
        self.pending_until = None
        if self.player:
            self.player.stop()
        self.loop_count = 0
        self.revision += 1

    def seek(self, seconds, now):
        self.require_song()
        seconds = number(seconds, "跳转位置")
        if seconds > self.song.score.duration:
            raise ScoreError("跳转位置超出曲目长度。")
        was_playing = self.player.state == "playing"
        deadline = self.pending_until
        self._put(seconds, "已跳转，开始后从这里继续")
        self.pending_until = deadline
        if was_playing:
            self.player.origin = now - self.player.position
            self.player.state = "playing"
            self.player.reason = "已跳转 · 演奏中"
        elif deadline is not None:
            self.player.reason = "已跳转 · 等待开始"
        if self.loop["enabled"] and not self.loop["start"] <= seconds < self.loop["end"]:
            self.loop["enabled"] = False
        self.loop_count = 0
        self.revision += 1

    def step_section(self, direction, now):
        self.require_song()
        if type(direction) is not int or direction not in (-1, 1):
            raise ScoreError("段落方向无效。")
        times = sorted({section["time"] for section in self.song.sections})
        if not times:
            raise ScoreError("此曲尚未设置段落。")
        position = self.position(now)
        if direction > 0:
            index = bisect_right(times, position + 1e-6)
            target = times[min(index, len(times) - 1)]
        else:
            index = bisect_right(times, position + 1e-6) - 1
            if index >= 0 and position - times[index] > 0.5:
                target = times[index]
            else:
                target = times[max(0, index - 1)]
        self.seek(target, now)

    def require_song(self):
        if self.song is None:
            raise ScoreError("请先选择曲目。")
        if self.closed:
            raise ScoreError("演奏台已关闭，请重新启动。")

    def event_keys(self, index, event):
        """One immutable master timeline; voice selection only gates future strikes."""
        if self.part == "all":
            return event.keys
        return self.song.events[index][self.part]

    def switch_part(self, part):
        self.require_song()
        if part not in ("all", "melody", "accompaniment"):
            raise ScoreError("未知声部。")
        if part != "all" and not self.song.has_parts:
            raise ScoreError("此曲尚未标注声部，支持完整演奏。")
        if part != "all" and not self.song.parts[part].events:
            raise ScoreError("所选声部没有音符。")
        if part == self.part:
            return
        self.part = part
        label = {"all": "完整演奏", "melody": "只弹旋律", "accompaniment": "只弹伴奏"}[part]
        self.player.reason = label + " · 已切换声部"
        # Deliberately preserve origin, index, pending countdown, key-up deadline,
        # loop count and revision. Already-struck notes must never be retriggered.

    def configure(self, values, now):
        self.require_song()
        if not isinstance(values, dict):
            raise ScoreError("设置应为一个对象。")
        if set(values) - {"speed", "part", "output", "countdown", "loop", "background"}:
            raise ScoreError("包含未知设置。")
        if set(values) == {"part"}:
            self.switch_part(values["part"])
            return
        if set(values) == {"background"}:
            if type(values["background"]) is not bool:
                raise ScoreError("后台演奏开关应为 true 或 false。")
            self.background = values["background"]
            if self.keyboard.real is not None:
                self.keyboard.real.allow_background = self.background
            return
        speed = number(values.get("speed", self.speed), "速度", positive=True)
        if not 0.25 <= speed <= 2:
            raise ScoreError("速度范围是 0.25 到 2 倍。")
        part, output = values.get("part", self.part), values.get("output", self.output)
        if part not in ("all", "melody", "accompaniment"):
            raise ScoreError("未知声部。")
        if part != "all" and not self.song.has_parts:
            raise ScoreError("此曲尚未标注声部，支持完整演奏。")
        if part != "all" and not self.song.parts[part].events:
            raise ScoreError("所选声部没有音符。")
        if output not in ("game", "preview"):
            raise ScoreError("未知演奏输出。")
        if output == "game" and not self.game_available:
            raise ScoreError("游戏按键未连接，请使用管理员入口。")
        countdown = number(values.get("countdown", self.countdown), "倒计时")
        if not 0 <= countdown <= 15:
            raise ScoreError("倒计时范围是 0 到 15 秒。")
        background = values.get("background", self.background)
        if type(background) is not bool:
            raise ScoreError("后台演奏开关应为 true 或 false。")
        loop = dict(self.loop)
        if "loop" in values:
            if not isinstance(values["loop"], dict) or set(values["loop"]) - {
                "enabled",
                "start",
                "end",
            }:
                raise ScoreError("无效的循环设置。")
            loop.update(values["loop"])
            if type(loop["enabled"]) is not bool:
                raise ScoreError("循环开关应为 true 或 false。")
            loop["start"], loop["end"] = (
                number(loop["start"], "循环起点"),
                number(loop["end"], "循环终点"),
            )
            if (
                not 0 <= loop["start"] < loop["end"] <= self.song.score.duration
                or loop["end"] - loop["start"] < 0.25
            ):
                raise ScoreError("循环区间至少 0.25 秒，且必须在曲目范围内。")
        prepared = prepare_score(self.song.score, speed)
        choose_tap_seconds(prepared)
        pos = self.position(now)
        self.pause(now)
        self.speed, self.part, self.output, self.countdown, self.loop, self.background = (
            speed,
            part,
            output,
            countdown,
            loop,
            background,
        )
        self.keyboard.output = output
        if self.keyboard.real is not None:
            self.keyboard.real.allow_background = background
        self.player = Playback(prepared, self.keyboard, countdown=0)
        self.player.event_filter = self.event_keys
        self._put(pos, "设置已更新，开始后继续")
        self.loop_count = 0

    def start(self, now):
        self.require_song()
        if self.player.state == "playing" or self.pending_until is not None:
            return
        pos = self.position(now)
        if self.loop["enabled"] and not self.loop["start"] <= pos < self.loop["end"]:
            self._put(self.loop["start"])
        elif pos >= self.song.score.duration or self.player.state == "finished":
            self._put(self.loop["start"] if self.loop["enabled"] else 0)
        # Web-start countdown does not require the game to have focus until it ends.
        # We never activate or steal focus from another application.
        delay = self.countdown if self.output == "game" else 0
        self.pending_until = now + delay
        self.player.reason = (
            "即将开始演奏" if self.output == "game" and self.countdown == 0
            else "请切回游戏乐器界面" if self.output == "game" and not self.background
            else "准备演奏" if self.output == "game"
            else "准备试听"
        )
        self.revision += 1
        if delay == 0:
            self.tick(now)

    def toggle(self, now):
        if self.player and (self.player.state == "playing" or self.pending_until is not None):
            self.pause(now)
        else:
            self.start(now)

    def tick(self, now):
        if not self.player or self.closed:
            return
        if self.pending_until is not None:
            if now < self.pending_until:
                return
            self.pending_until = None
            if not self.keyboard.arm():
                self.pause(now, "未找到游戏窗口，请打开游戏乐器界面后按 F8")
                return
            self.player.toggle(now, True)
        p = self.player
        if p.state == "playing" and self.loop["enabled"]:
            end = self.loop["end"] / self.speed
            elapsed = max(0, now - p.origin)
            if elapsed + 1e-9 >= end:
                if elapsed - end > p.max_lateness:
                    self._put(self.loop["start"], "循环遇到调度延迟，按 F8 继续")
                    self.revision += 1
                    return
                p.close()
                p.origin += (self.loop["end"] - self.loop["start"]) / self.speed
                p.position = self.loop["start"] / self.speed
                p.index = bisect_left([e.time for e in p.score.events], p.position - 1e-8)
                self.loop_count += 1
        state = p.state
        p.tick(now, self.keyboard.target_ready() if p.state in ("playing", "countdown") else True)
        if p.state != state:
            self.revision += 1

    def close(self, now):
        self.pause(now, "演奏台已关闭")
        self.closed = True
        if self.player:
            self.player.close()

    def snapshot(self, now):
        p = self.player
        return dict(
            song_id=self.song.id if self.song else None,
            state="closed"
            if self.closed
            else "countdown"
            if self.pending_until is not None
            else p.state
            if p
            else "empty",
            reason=p.reason if p else "请选择曲目",
            position=self.position(now),
            duration=self.song.score.duration if self.song else 0,
            speed=self.speed,
            part=self.part,
            output=self.output,
            countdown=self.countdown,
            background=self.background,
            countdown_remaining=max(0, self.pending_until - now)
            if self.pending_until is not None
            else 0,
            loop=dict(self.loop),
            loop_count=self.loop_count,
            revision=self.revision,
            game_available=self.game_available,
            emitted_groups=p.index if p else 0,
            total_groups=len(p.score.events) if p else 0,
            server_time=now,
        )


class Engine:
    def __init__(self, library, demo=False):
        self.library, self.demo = library, demo
        self.commands = queue.Queue(maxsize=128)
        self.stop_event, self.ready = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.latest = {}
        self.controller = None
        self.thread = threading.Thread(target=self.run, name="lyre-input", daemon=True)

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("演奏引擎启动超时。")

    def submit(self, action, payload=None):
        if self.stop_event.is_set():
            raise ScoreError("演奏台已退出，请重新启动。")
        future = Future()
        try:
            self.commands.put_nowait((action, payload or {}, future))
        except queue.Full:
            raise ScoreError("操作太快，请稍后重试。")
        try:
            return future.result(timeout=5)
        except FutureTimeoutError:
            future.cancel()
            raise ScoreError("操作超时，请查看连接状态。")

    def snapshot(self):
        with self.lock:
            status = dict(self.latest)
        if not status:
            return status
        now = time.perf_counter()
        delta = max(0, now - status["server_time"])
        if status["state"] == "playing":
            pos = status["position"] + delta * status["speed"]
            loop = status["loop"]
            if loop["enabled"] and pos >= loop["end"]:
                count = 1 + int((pos - loop["end"]) / (loop["end"] - loop["start"]))
                pos -= count * (loop["end"] - loop["start"])
                status["loop_count"] += count
            status["position"] = min(status["duration"], pos)
        if status["state"] == "countdown":
            status["countdown_remaining"] = max(0, status["countdown_remaining"] - delta)
        status["server_time"] = now
        return status

    def run(self):
        real, backend_error, administrator = None, "", False
        if not self.demo:
            try:
                import ctypes
                from .win_input import WindowsKeyboard

                real = WindowsKeyboard()
                real.__enter__()
                administrator = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception as exc:
                real = None
                backend_error = str(exc)
        keyboard = OutputKeyboard(real)
        c = self.controller = Controller(keyboard, real is not None)
        entries = self.library.list()
        first = next((e for e in entries if not e.get("error") and e["id"].startswith("03_")), None)
        if first is None:
            first = next((e for e in entries if not e.get("error")), None)
        if first:
            c.select(self.library.load(first["id"]), time.perf_counter())
        next_status = 0
        try:
            while not self.stop_event.is_set():
                now = time.perf_counter()
                dirty = False
                # F9/F10 are handled after queued requests, so a stop always wins this tick.
                for unused in range(16):
                    try:
                        action, payload, future = self.commands.get_nowait()
                    except queue.Empty:
                        break
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        self.dispatch(action, payload, now)
                        future.set_result(c.snapshot(now))
                    except Exception as exc:
                        future.set_exception(exc)
                    dirty = True
                if real:
                    keys = real.hotkeys()
                    self.handle_hotkeys(keys, now)
                    dirty = dirty or bool(keys)
                try:
                    c.tick(now)
                except (OSError, RuntimeError, ScoreError) as exc:
                    try:
                        c.pause(now, "演奏已暂停：{}".format(exc))
                    except OSError:
                        pass
                    backend_error = str(exc)
                    dirty = True
                if now >= next_status or dirty or not self.ready.is_set():
                    status = c.snapshot(now)
                    status.update(
                        demo=self.demo, administrator=administrator, backend_error=backend_error
                    )
                    with self.lock:
                        self.latest = status
                    self.ready.set()
                    next_status = now + 0.04
                self.stop_event.wait(
                    0.002
                    if c.player and (c.player.state == "playing" or c.pending_until)
                    else 0.015
                )
        finally:
            try:
                c.close(time.perf_counter())
            finally:
                if real:
                    real.__exit__(None, None, None)
                self.ready.set()

    def dispatch(self, action, payload, now):
        c = self.controller
        if action == "select":
            c.select(self.library.load(payload.get("id")), now)
        elif action == "song_step":
            self.step_song(payload.get("direction"), now)
        elif action == "section_step":
            c.step_section(payload.get("direction"), now)
        elif action == "start":
            c.start(now)
        elif action == "pause":
            c.pause(now)
        elif action == "stop":
            c.stop(now)
        elif action == "seek":
            c.seek(payload.get("position"), now)
        elif action == "configure":
            c.configure(payload, now)
        elif action == "shutdown":
            c.close(now)
            self.stop_event.set()
        else:
            raise ScoreError("未知操作。")

    def step_song(self, direction, now):
        if type(direction) is not int or direction not in (-1, 1):
            raise ScoreError("歌曲方向无效。")
        songs = [entry["id"] for entry in self.library.list() if not entry.get("error")]
        if not songs:
            raise ScoreError("曲库中还没有可演奏的歌曲。")
        current = self.controller.song.id if self.controller.song else None
        if current in songs:
            index = (songs.index(current) + direction) % len(songs)
        else:
            index = 0 if direction > 0 else len(songs) - 1
        self.controller.select(self.library.load(songs[index]), now)

    def handle_hotkeys(self, keys, now):
        """Quit and stop take priority over every other queued shortcut."""
        c = self.controller
        if "quit" in keys:
            c.close(now)
            self.stop_event.set()
            return
        if "stop" in keys:
            c.stop(now)
            return
        try:
            if "toggle" in keys:
                c.toggle(now)
            for part in ("all", "melody", "accompaniment"):
                if part in keys:
                    c.switch_part(part)
                    break
            for key, direction in (("section_previous", -1), ("section_next", 1)):
                if key in keys:
                    c.step_section(direction, now)
                    break
            for key, direction in (("song_previous", -1), ("song_next", 1)):
                if key in keys:
                    self.step_song(direction, now)
                    break
        except ScoreError as exc:
            if c.player:
                c.player.reason = str(exc)

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
