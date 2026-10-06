"""Read independent JSON scores. This module never accesses the keyboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple
import json
import math

KEY_ORDER = "ZXCVBNMASDFGHJQWERTYU"
KEY_SET = frozenset(KEY_ORDER)


class ScoreError(ValueError):
    pass


@dataclass(frozen=True)
class Event:
    time: float  # Seconds at the score's original tempo.
    keys: Tuple[str, ...]


@dataclass(frozen=True)
class Score:
    title: str
    bpm: float
    events: Tuple[Event, ...]
    duration: float

    @property
    def note_count(self):
        return sum(len(event.keys) for event in self.events)


def number(value, label, minimum=0.0, positive=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ScoreError("{} 必须是数字。".format(label))
    value = float(value)
    if not math.isfinite(value) or value < minimum or (positive and value <= 0):
        raise ScoreError("{} 的数值无效。".format(label))
    return value


def parse_keys(value, label):
    if isinstance(value, str):
        values = list("".join(value.upper().split()))
    elif isinstance(value, list) and all(isinstance(x, str) and len(x) == 1 for x in value):
        values = [x.upper() for x in value]
    else:
        raise ScoreError("{} 应为字母字符串或单个字母组成的数组。".format(label))
    if not values:
        raise ScoreError("{} 不能为空；休止请用时间间隔表示。".format(label))
    invalid = set(values) - KEY_SET
    if invalid:
        raise ScoreError("{} 包含不支持的琴键：{}".format(label, ", ".join(sorted(invalid))))
    if len(set(values)) != len(values):
        raise ScoreError("{} 中同一个琴键重复出现。".format(label))
    return tuple(k for k in KEY_ORDER if k in values)


def load_score(path):
    path = Path(path)
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ScoreError("谱文件超过 10 MB，无法读取。")
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScoreError("无法读取谱文件 {}：{}".format(path, exc)) from exc
    return score_from_data(data, path.stem)


def score_from_data(data, fallback_title="未命名曲谱"):
    if not isinstance(data, dict):
        raise ScoreError("谱文件最外层必须是 JSON 对象。")
    title = data.get("title", fallback_title)
    if not isinstance(title, str) or not title.strip():
        raise ScoreError("title 必须是非空文字。")
    bpm = number(data.get("bpm"), "bpm", positive=True)
    rows = data.get("events")
    if not isinstance(rows, list) or not rows:
        raise ScoreError("events 必须是非空数组。")
    if len(rows) > 200000:
        raise ScoreError("谱文件的音符事件过多。")

    if data.get("format") == "lyre-score":
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ScoreError("不支持此谱文件的 version；当前支持 1。")
        unit = data.get("time_unit", "beats")
        if unit not in ("beats", "seconds"):
            raise ScoreError("time_unit 只能是 beats 或 seconds。")
        scale = 60.0 / bpm if unit == "beats" else 1.0
        legacy = False
    elif "format" not in data and all(
        isinstance(r, dict) and "time" in r and "key" in r for r in rows
    ):
        # Compatibility with the arrangement JSON made in this workspace.
        scale, legacy = 1.0, True
    else:
        raise ScoreError("未知谱文件格式；请使用 format=lyre-score、version=1。")

    merged = {}
    inferred_end = 0.0
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ScoreError("第 {} 条事件必须是对象。".format(i))
        at = number(row.get("time" if legacy else "at"), "第 {} 条事件的时间".format(i)) * scale
        keys = parse_keys(row.get("key" if legacy else "keys"), "第 {} 条事件的 keys".format(i))
        # Microsecond grouping removes floating point noise without moving musical onsets.
        timestamp = round(at * 1000000)
        merged.setdefault(timestamp, set()).update(keys)
        tail = (
            number(row.get("duration", 60.0 / bpm), "第 {} 条事件的 duration".format(i))
            if legacy
            else 60.0 / bpm
        )
        inferred_end = max(inferred_end, at + tail)
    events = tuple(
        Event(t / 1000000.0, tuple(k for k in KEY_ORDER if k in keys))
        for t, keys in sorted(merged.items())
    )
    duration = number(data["end"], "end") * scale if "end" in data else inferred_end
    if duration <= events[-1].time:
        raise ScoreError("end 必须晚于最后一次按键。")
    return Score(title.strip(), bpm, events, duration)


def score_to_data(score):
    """Use beat positions so a future score can be edited or played at a new BPM."""
    factor = score.bpm / 60.0
    return {
        "format": "lyre-score",
        "version": 1,
        "title": score.title,
        "bpm": score.bpm,
        "time_unit": "beats",
        "end": round(score.duration * factor, 6),
        "events": [
            {"at": round(e.time * factor, 6), "keys": "".join(e.keys)} for e in score.events
        ],
    }


def write_score(score, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(score_to_data(score), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def prepare_score(score, speed=1.0):
    speed = number(speed, "速度倍率", positive=True)
    return Score(
        score.title,
        score.bpm * speed,
        tuple(Event(e.time / speed, e.keys) for e in score.events),
        score.duration / speed,
    )


def choose_tap_seconds(score, milliseconds=50.0):
    requested = number(milliseconds, "按键时长", positive=True) / 1000.0
    if not 0.01 <= requested <= 0.2:
        raise ScoreError("按键时长应为 10～200 毫秒。")
    gaps = [b.time - a.time for a, b in zip(score.events, score.events[1:])]
    smallest = min(gaps, default=1.0)
    if smallest < 0.02:
        raise ScoreError("当前速度下有两组按键间隔小于 20 毫秒，请降低速度。")
    return min(requested, smallest * 0.6)
