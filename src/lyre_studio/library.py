"""Song library and explicit melody/accompaniment metadata; no keyboard access."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import json
import threading
import uuid

from .scores import Score, Event, ScoreError, KEY_ORDER, number, parse_keys, score_from_data

MAX_FILE_BYTES = 10 * 1024 * 1024
LEAD_ROLES = frozenset(("melody", "riff", "reply"))
BACK_ROLES = frozenset(("bass", "chord", "accomp", "harmony"))


class Song:
    def __init__(self, identity, data):
        self.id = identity
        self.score = score_from_data(data, Path(identity).stem)
        legacy = data.get("format") != "lyre-score"
        scale = 1.0 if legacy or data.get("time_unit") == "seconds" else 60.0 / self.score.bpm
        rows = data["events"]
        explicit = any("parts" in row for row in rows)
        roles = legacy and all(row.get("role") in LEAD_ROLES | BACK_ROLES for row in rows)
        self.has_parts = explicit or roles
        separated = {"melody": defaultdict(set), "accompaniment": defaultdict(set)}
        if self.has_parts:
            for index, row in enumerate(rows, 1):
                stamp = round(row["time" if legacy else "at"] * scale * 1000000)
                keys = set(parse_keys(row["key" if legacy else "keys"], "keys"))
                if explicit:
                    parts = row.get("parts")
                    if not isinstance(parts, dict) or set(parts) - set(separated):
                        raise ScoreError(
                            "第 {} 条事件的 parts 应只含 melody、accompaniment。".format(index)
                        )
                    union = set()
                    for part in separated:
                        value = parts.get(part, "")
                        selected = (
                            set() if value == "" or value == [] else set(parse_keys(value, part))
                        )
                        separated[part][stamp].update(selected)
                        union.update(selected)
                    if union != keys:
                        raise ScoreError("第 {} 条事件的声部琴键必须与 keys 一致。".format(index))
                else:
                    part = "melody" if row["role"] in LEAD_ROLES else "accompaniment"
                    separated[part][stamp].update(keys)
        self.parts = {"all": self.score}
        self.events = []
        for part, groups in separated.items():
            events = tuple(
                Event(t / 1000000, tuple(k for k in KEY_ORDER if k in keys))
                for t, keys in sorted(groups.items())
                if keys
            )
            self.parts[part] = Score(self.score.title, self.score.bpm, events, self.score.duration)
        for e in self.score.events:
            stamp = round(e.time * 1000000)
            self.events.append(
                dict(
                    time=e.time,
                    keys="".join(e.keys),
                    melody="".join(k for k in KEY_ORDER if k in separated["melody"].get(stamp, ())),
                    accompaniment="".join(
                        k for k in KEY_ORDER if k in separated["accompaniment"].get(stamp, ())
                    ),
                )
            )
        self.sections = []
        source_sections = data.get("sections", [])
        if not isinstance(source_sections, list):
            raise ScoreError("sections 应是数组。")
        for section in source_sections:
            if not isinstance(section, dict) or not isinstance(section.get("name"), str):
                raise ScoreError("段落必须包含 name 和 at。")
            at = number(section.get("at"), "段落 at") * scale
            if at >= self.score.duration:
                raise ScoreError("段落起点不能晚于曲目结束。")
            self.sections.append(dict(name=section["name"][:120], time=at))
        self.sections.sort(key=lambda s: s["time"])
        self.description = str(data.get("description", ""))[:1000]
        self.beat_origin = number(data.get("beat_origin", 0), "beat_origin") * scale
        self.beats_per_bar = data.get("beats_per_bar", 4)
        if type(self.beats_per_bar) is not int or not 1 <= self.beats_per_bar <= 16:
            raise ScoreError("beats_per_bar 应是 1 到 16 的整数。")

    def summary(self):
        return dict(
            id=self.id,
            title=self.score.title,
            bpm=self.score.bpm,
            duration=self.score.duration,
            notes=self.score.note_count,
            events=len(self.score.events),
            has_parts=self.has_parts,
            part_counts={p: s.note_count for p, s in self.parts.items()},
            description=self.description,
            sections=self.sections,
            beat_origin=self.beat_origin,
            beats_per_bar=self.beats_per_bar,
        )

    def detail(self):
        return dict(self.summary(), score=self.events)


class Library:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.cache = {}

    def files(self):
        return sorted(self.directory.glob("*.json"), key=lambda p: p.name.casefold())

    def load(self, identity):
        with self.lock:
            if (
                not isinstance(identity, str)
                or not identity
                or Path(identity).name != identity
                or "/" in identity
                or "\\" in identity
            ):
                raise ScoreError("无效的曲目编号。")
            path = self.directory / identity
            if path.suffix.lower() != ".json" or path.resolve().parent != self.directory.resolve():
                raise ScoreError("曲目必须位于 songs 文件夹中。")
            try:
                stat = path.stat()
                if stat.st_size > MAX_FILE_BYTES:
                    raise ScoreError("谱文件不能超过 10 MB。")
                signature = (stat.st_mtime_ns, stat.st_size)
                if identity not in self.cache or self.cache[identity][0] != signature:
                    data = json.loads(path.read_text(encoding="utf-8-sig"))
                    self.cache[identity] = (signature, Song(identity, data))
                return self.cache[identity][1]
            except (OSError, ValueError, UnicodeError) as exc:
                if isinstance(exc, ScoreError):
                    raise
                raise ScoreError("无法读取曲谱：{}".format(exc)) from exc

    def list(self):
        entries = []
        for path in self.files():
            try:
                entries.append(self.load(path.name).summary())
            except ScoreError as exc:
                entries.append(dict(id=path.name, title=path.stem, error=str(exc)))
        return entries

    def import_data(self, data):
        # Validate before writing, and always use a new application-generated name.
        identity = "song_{}.json".format(uuid.uuid4().hex[:12])
        song = Song(identity, data)
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            raise ScoreError("谱文件不能超过 10 MB。")
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / identity).open("x", encoding="utf-8") as output:
                output.write(text)
        return song
