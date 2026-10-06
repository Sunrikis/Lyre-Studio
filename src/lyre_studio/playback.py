"""Clock-driven player with an injected keyboard backend, also usable in tests."""

from __future__ import annotations

from .scores import choose_tap_seconds


class Playback:
    def __init__(self, score, keyboard, countdown=3.0, tap_ms=50.0, max_lateness=0.12):
        self.score = score
        self.keyboard = keyboard
        self.countdown = countdown
        self.tap_seconds = choose_tap_seconds(score, tap_ms)
        self.max_lateness = max_lateness
        self.state = "ready"
        self.reason = "等待 F8"
        self.index = 0
        self.position = 0.0
        self.origin = 0.0
        self.countdown_end = 0.0
        self.releasing_at = None
        # Filtering never changes event indices or the transport clock.
        # Signature: (event_index, event) -> iterable of keys, possibly empty.
        self.event_filter = None

    def elapsed(self, now):
        if self.state == "playing":
            return min(self.score.duration, max(0.0, now - self.origin))
        return self.position

    def _release(self):
        self.keyboard.release_all()
        self.releasing_at = None

    def pause(self, now, reason="已暂停，按 F8 继续"):
        if self.state == "playing":
            self.position = self.elapsed(now)
            if self.index < len(self.score.events):
                # Never skip a pending event or burst out overdue chords on resuming.
                self.position = min(self.position, self.score.events[self.index].time)
        self._release()
        self.state, self.reason = "paused", reason

    def toggle(self, now, target_ready=True):
        if self.state in ("playing", "countdown"):
            self.pause(now)
            return
        if not target_ready:
            self.reason = "请先切回游戏的乐器界面，再按 F8。"
            return
        if self.state in ("ready", "finished"):
            self.position, self.index = 0.0, 0
        self.countdown_end = now + self.countdown
        self.state, self.reason = "countdown", "准备开始"

    def stop(self):
        self._release()
        self.index, self.position = 0, 0.0
        self.state, self.reason = "ready", "已停止归零，按 F8 重新开始"

    def tick(self, now, target_ready=True):
        if self.releasing_at is not None and now >= self.releasing_at:
            self._release()
        if self.state not in ("playing", "countdown"):
            return
        if not target_ready:
            self.pause(now, "游戏失去焦点或修饰键被按下；切回游戏后按 F8 继续。")
            return
        if self.state == "countdown":
            if now < self.countdown_end:
                return
            # A delayed countdown should not consume the beginning of the song.
            self.origin = now - self.position
            self.state, self.reason = "playing", "演奏中"
        elapsed = self.elapsed(now)
        if self.index < len(self.score.events):
            event = self.score.events[self.index]
            # Subtracting two clock readings can round an exact beat downward.
            if elapsed + 1e-9 >= event.time:
                multiple_due = (
                    self.index + 1 < len(self.score.events)
                    and elapsed + 1e-9 >= self.score.events[self.index + 1].time
                )
                if multiple_due or elapsed - event.time > self.max_lateness:
                    self.pause(now, "系统调度延迟较大，已暂停；按 F8 从待演音符继续。")
                    return
                self._release()
                keys = (
                    event.keys
                    if self.event_filter is None
                    else tuple(self.event_filter(self.index, event))
                )
                try:
                    # Backend rechecks the exact foreground window immediately before input.
                    if keys:
                        self.keyboard.press(keys)
                except FocusChanged:
                    self.pause(now, "前台窗口发生变化；切回游戏后按 F8 继续。")
                    return
                self.index += 1
                self.releasing_at = now + self.tap_seconds if keys else None
        if (
            self.index == len(self.score.events)
            and elapsed + 1e-9 >= self.score.duration
            and self.releasing_at is None
        ):
            self.position = self.score.duration
            self.state, self.reason = "finished", "演奏结束；F8 再听一次，F10 退出。"

    def close(self):
        self._release()


class FocusChanged(RuntimeError):
    pass
