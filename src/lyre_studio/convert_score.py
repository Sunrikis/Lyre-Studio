"""Validate and convert scores while retaining practice metadata."""

import argparse
import json
from pathlib import Path
import sys
from .library import Song, MAX_FILE_BYTES


def convert(data, name="song"):
    song = Song(name, data)
    if data.get("format") == "lyre-score":
        return data
    events = []
    for row in song.events:
        event = dict(at=row["time"], keys=row["keys"])
        if song.has_parts:
            event["parts"] = {part: row[part] for part in ("melody", "accompaniment")}
        events.append(event)
    return dict(
        format="lyre-score",
        version=1,
        title=song.score.title,
        bpm=song.score.bpm,
        time_unit="seconds",
        end=song.score.duration,
        events=events,
        description=song.description,
        sections=[dict(name=s["name"], at=s["time"]) for s in song.sections],
        beat_origin=song.beat_origin,
        beats_per_bar=song.beats_per_bar,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="转换曲谱并保留旋律、伴奏及段落。")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("谱文件不能超过 10 MB。")
        data = convert(json.loads(args.source.read_text(encoding="utf-8-sig")), args.source.stem)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w" if args.overwrite else "x", encoding="utf-8") as output:
            output.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        print("已保存：{}".format(args.output))
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
