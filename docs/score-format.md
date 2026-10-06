# 曲谱格式

使用 UTF-8 JSON，每首歌一个文件。最小分声部示例：

```json
{
  "format": "lyre-score",
  "version": 1,
  "title": "练习",
  "bpm": 100,
  "time_unit": "beats",
  "end": 4,
  "events": [
    {"at": 0, "keys": "NADH", "parts": {"melody": "H", "accompaniment": "NAD"}},
    {"at": 1, "keys": "J", "parts": {"melody": "J", "accompaniment": ""}}
  ],
  "sections": [{"name": "第一段", "at": 0}],
  "beat_origin": 0,
  "beats_per_bar": 4
}
```

`at` 是起音位置，`end` 是整曲结束位置。`time_unit` 可为 `beats`（拍）或 `seconds`（秒）；段落和 `beat_origin` 使用相同单位。`bpm` 为正数，`beats_per_bar` 为 1–16 的整数。休止通过事件之间的间隔表示。

`keys` 只包含 21 个琴键：低音 `ZXCVBNM`、中音 `ASDFGHJ`、高音 `QWERTYU`。一个字符串中的多个键同时弹奏。琶音写成多个不同时间的事件。按键时长由演奏器统一控制，并非持续按住就能延长游戏音色。

`parts` 可省略，此时只能完整演奏。若需要跟练，每条事件都须有 `parts`；两个声部的琴键并集必须等于 `keys`，空声部写 `""`。不要根据音区自动推断旋律，低音也可能属于旋律。

可选 `description` 是曲目说明，`sections` 用于段落跳转。详细示例见 `src/lyre_studio/examples/practice.json`。网页导入会先校验，并生成独立文件名。

安装后可用 `lyre-score 输入.json 输出.json` 转换早期包含 `time`、`key`、`role` 的编配谱。转换会保留声部和段落；覆盖已有文件需加 `--overwrite`。
