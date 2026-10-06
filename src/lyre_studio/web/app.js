'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="lyre-token"]').content;
const keyOrder = 'ZXCVBNMASDFGHJQWERTYU';
const midi = [48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83];
const pitch = Object.fromEntries([...keyOrder].map((k, i) => [k, midi[i]]));
let songs = [],
  song = null,
  state = null,
  anchor = performance.now(),
  online = false;
let seeking = false,
  speedEditing = false,
  loopEditing = false,
  loadId = '',
  songRequest = 0,
  toastTimer = 0,
  lastUpcoming = '';
let pollTimer = 0,
  pollInFlight = false,
  commandQueue = Promise.resolve(),
  lastDraw = 0,
  pendingCommands = 0,
  requestSerial = 0;
const manualKeys = new Map();

function timeText(value, decimal = false) {
  value = Math.max(0, Number(value) || 0);
  value = decimal ? Math.round(value * 10) / 10 : Math.floor(value);
  const minutes = Math.floor(value / 60),
    seconds = value % 60;
  return (
    String(minutes).padStart(2, '0') +
    ':' +
    (decimal ? seconds.toFixed(1).padStart(4, '0') : String(Math.floor(seconds)).padStart(2, '0'))
  );
}
function parseTime(text) {
  const match = String(text)
    .trim()
    .match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!match) throw Error('时间格式请使用 01:23.5 或秒数。');
  const result = Number(match[1] || 0) * 60 + Number(match[2]);
  if (!Number.isFinite(result)) throw Error('时间无效。');
  return result;
}
function loopTime(value) {
  const ms = Math.floor((Number(value) || 0) * 1000 + 1e-6);
  return (
    String(Math.floor(ms / 60000)).padStart(2, '0') +
    ':' +
    ((ms % 60000) / 1000).toFixed(3).padStart(6, '0')
  );
}
function shortTitle(title) {
  return title.replace(/\s*[—｜]\s*21键.*$/, '');
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $('toast').textContent = text;
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  toastTimer = setTimeout(() => ($('toast').hidden = true), 4500);
}
async function api(path, body) {
  const response = await fetch(path, {
    cache: 'no-store',
    headers: body ? { 'Content-Type': 'application/json', 'X-Lyre-Token': token } : {},
    method: body ? 'POST' : 'GET',
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(6000),
  });
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '操作没有完成。');
  return data;
}
function command(action, payload = {}) {
  pendingCommands++;
  requestSerial++;
  const task = commandQueue.then(async () => {
    const started = performance.now();
    const next = await api('/api/command', { action, payload });
    applyState({ ...state, ...next }, (performance.now() - started) / 2000);
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, 0);
    if (action === 'shutdown') disconnect('演奏台已退出；重新打开启动入口即可连接。');
    return next;
  });
  commandQueue = task
    .catch((error) => toast(error.message, true))
    .finally(() => {
      pendingCommands--;
    });
  return commandQueue;
}
function configure(values) {
  return command('configure', values);
}

async function refreshLibrary() {
  try {
    songs = (await api('/api/library')).songs;
    renderLibrary();
  } catch (error) {
    toast('曲库读取失败：' + error.message, true);
  }
}
function renderLibrary() {
  const query = $('search').value.trim().toLocaleLowerCase();
  const fragment = document.createDocumentFragment();
  for (const item of songs.filter((s) =>
    (s.title + ' ' + s.id).toLocaleLowerCase().includes(query),
  )) {
    const button = document.createElement('button');
    button.className = 'song-card' + (item.id === state?.song_id ? ' selected' : '');
    button.setAttribute('aria-pressed', String(item.id === state?.song_id));
    button.disabled = Boolean(item.error);
    const cover = document.createElement('span');
    cover.className = 'mini-cover';
    cover.textContent = shortTitle(item.title)[0] || '♪';
    const info = document.createElement('span');
    info.className = 'track-info';
    const name = document.createElement('strong');
    name.textContent = shortTitle(item.title);
    const meta = document.createElement('small');
    meta.textContent = item.error
      ? '文件格式需要修正'
      : `${timeText(item.duration)} · ${item.bpm} BPM · ${item.has_parts ? '可分声部' : '完整声部'}`;
    info.append(name, meta);
    button.append(cover, info);
    button.title = item.error || item.title;
    button.addEventListener('click', () => selectSong(item.id));
    fragment.append(button);
  }
  if (!fragment.childNodes.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = '没有找到曲目。试试其他关键词，或导入一个 JSON 曲谱。';
    fragment.append(empty);
  }
  $('library').replaceChildren(fragment);
  $('songCount').textContent = songs.filter((s) => !s.error).length;
}
async function selectSong(id) {
  await command('select', { id });
}
async function loadSong(id) {
  if (loadId === id) return;
  loadId = id;
  const request = ++songRequest;
  try {
    const next = await api('/api/song?id=' + encodeURIComponent(id));
    if (request !== songRequest || state?.song_id !== id) return;
    song = next;
    lastUpcoming = '';
    synth.reset(current().position, state.loop_count);
    $('songTitle').textContent = shortTitle(song.title);
    $('coverLetter').textContent = shortTitle(song.title)
      .split(/\s+/)
      .map((s) => s[0])
      .slice(0, 2)
      .join('');
    $('songMeta').textContent =
      `${song.bpm} BPM 原速 · ${timeText(song.duration)} · ${song.notes} 个音`;
    $('totalTime').textContent = timeText(song.duration);
    $('seek').max = song.duration;
    $('partsBadge').textContent = song.has_parts ? '已分声部' : '未标注声部';
    $('export').href = '/api/export?id=' + encodeURIComponent(song.id);
    const chips = document.createDocumentFragment();
    for (const section of song.sections) {
      const b = document.createElement('button');
      b.textContent = section.name;
      b.dataset.time = section.time;
      b.title = timeText(section.time);
      b.addEventListener('click', () => command('seek', { position: section.time }));
      chips.append(b);
    }
    $('sections').replaceChildren(chips);
    renderState();
    drawTimeline();
  } catch (error) {
    if (request === songRequest) {
      loadId = '';
      toast(error.message, true);
    }
  }
}

function current() {
  if (!state) return { position: 0, cycle: 0 };
  let position = state.position,
    cycle = state.loop_count;
  if (state.state === 'playing')
    position += (Math.max(0, performance.now() - anchor) / 1000) * state.speed;
  if (state.loop.enabled && state.state === 'playing' && position >= state.loop.end) {
    const loops = 1 + Math.floor((position - state.loop.end) / (state.loop.end - state.loop.start));
    position -= loops * (state.loop.end - state.loop.start);
    cycle += loops;
  }
  return { position: Math.min(state.duration, position), cycle };
}
function applyState(next, latency = 0) {
  const differentSong = state?.song_id !== next.song_id;
  const differentPart = state?.part !== next.part;
  const starting = !differentSong && state?.state === 'countdown' && next.state === 'playing';
  const audioStart = starting ? state.position : next.position;
  const invalidate =
    state?.revision !== next.revision ||
    differentSong ||
    (state?.state === 'playing' && next.state !== 'playing');
  state = next;
  anchor = performance.now() - Math.min(latency, 0.08) * 1000;
  online = next.state !== 'closed';
  if (invalidate) synth.reset(audioStart, next.loop_count);
  else if (differentPart) synth.changePart();
  if (differentSong) {
    song = null;
    loadId = '';
    lastUpcoming = '';
    loopEditing = false;
    renderLibrary();
  }
  if (next.song_id && song?.id !== next.song_id) loadSong(next.song_id);
  renderState();
}
function renderState() {
  if (!state) return;
  $('connection').className = 'connection ' + (online ? 'online' : 'offline');
  $('connection').replaceChildren();
  const dot = document.createElement('i');
  $('connection').append(
    dot,
    document.createTextNode(
      !online
        ? '连接已断开'
        : state.demo
          ? '已连接 · 试听模式'
          : state.administrator
            ? '已连接 · 管理员'
            : state.game_available
              ? '已连接 · 普通权限'
              : '已连接 · 仅试听',
    ),
  );
  const backend = state.backend_error;
  const notice = backend
    ? '游戏按键未就绪：' + backend
    : state.demo
      ? '当前为网页试听模式，不向游戏发送按键。游戏演奏请使用“启动演奏台（管理员）”。'
      : state.game_available && !state.administrator
        ? '当前是普通权限。如果游戏无法响应按键，请退出后使用“启动演奏台（管理员）”。'
        : '';
  $('notice').hidden = !notice;
  $('notice').textContent = notice;
  document.body.classList.toggle('disabled-ui', !online);
  const names = {
    ready: '准备就绪',
    paused: '已暂停',
    playing: '演奏中',
    countdown: '准备开始',
    finished: '演奏完成',
    closed: '已退出',
    empty: '未选曲',
  };
  $('stateBadge').textContent = names[state.state] || '待机';
  $('stateBadge').className = 'state-badge ' + state.state;
  $('reason').textContent = state.reason;
  const active = ['playing', 'countdown'].includes(state.state);
  $('playText').textContent = active
    ? '暂停'
    : state.output === 'preview'
      ? '开始试听'
      : state.state === 'paused'
        ? '继续演奏'
        : '开始演奏';
  $('playIcon').textContent = active ? 'Ⅱ' : '▶';
  $('play').disabled = !song || !online;
  $('gameOutput').disabled = !state.game_available;
  document
    .querySelectorAll('[data-output]')
    .forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.output === state.output)));
  $('outputHint').textContent =
    state.output === 'game'
      ? state.countdown
        ? `开始后等待 ${state.countdown} 秒${state.background ? '；可在网页控制' : '切回游戏'}`
        : state.background
          ? '点击即开始；游戏窗口可留在后台（需实机确认兼容）'
          : '点击即开始；请保持游戏在前台'
      : '使用与游戏相同的谱，试听当前自动声部';
  $('background').checked = Boolean(state.background);
  $('background').disabled = !state.game_available;
  for (const id of ['previousSection', 'nextSection'])
    $(id).disabled = !song?.sections?.length || !online;
  document.querySelectorAll('[data-part]').forEach((b) => {
    b.setAttribute('aria-pressed', String(b.dataset.part === state.part));
    b.disabled =
      !song || (b.dataset.part !== 'all' && (!song.has_parts || !song.part_counts[b.dataset.part]));
  });
  const modeText = {
    all: '自动演奏全部声部；下方显示接下来的完整按键。',
    accompaniment: '你弹旋律，演奏器负责伴奏。下方显示你要弹的旋律按键。',
    melody: '你弹伴奏，演奏器负责旋律。下方显示你要弹的伴奏按键。',
  };
  $('practiceHint').textContent =
    song && !song.has_parts
      ? '此曲没有声部标签，暂时只能完整演奏。导入标注了 parts 的曲谱即可分声部。'
      : modeText[state.part];
  $('upcomingTitle').textContent =
    state.part === 'all'
      ? '接下来的按键'
      : state.part === 'accompaniment'
        ? '你来弹 · 旋律'
        : '你来弹 · 伴奏';
  if (!speedEditing) {
    $('speed').value = Math.round(state.speed * 100);
    showSpeed(state.speed);
  }
  document
    .querySelectorAll('[data-speed]')
    .forEach((b) => b.classList.toggle('active', Number(b.dataset.speed) === state.speed));
  if (document.activeElement !== $('countdown')) $('countdown').value = String(state.countdown);
  if (document.activeElement !== $('loopEnabled')) $('loopEnabled').checked = state.loop.enabled;
  if (!loopEditing) {
    $('loopA').value = loopTime(state.loop.start);
    $('loopB').value = loopTime(state.loop.end);
  }
  $('loopInfo').textContent = state.loop.enabled
    ? `区间已启用 · 已完成 ${state.loop_count} 轮`
    : '尚未开启循环';
  $('footerStatus').textContent =
    state.output === 'game' ? '游戏演奏 · F8 控制' : '网页试听 · 合成音色';
}
function showSpeed(speed) {
  $('speedBadge').textContent = speed + '×';
  $('bpm').textContent = song ? Number((song.bpm * speed).toFixed(1)) : '—';
}
function disconnect(message) {
  online = false;
  synth.reset();
  if (state) {
    state = { ...state, state: 'closed', reason: message };
    anchor = performance.now();
  }
  renderState();
  $('notice').hidden = false;
  $('notice').textContent = message || '本地演奏台已断开。重新打开启动入口，然后刷新本页。';
}
async function poll() {
  if (pollInFlight) return;
  pollInFlight = true;
  clearTimeout(pollTimer);
  const serial = requestSerial,
    started = performance.now();
  try {
    const next = await api('/api/status');
    if (serial === requestSerial && !pendingCommands)
      applyState(next, (performance.now() - started) / 2000);
  } catch (error) {
    if (online || !state) disconnect('本地演奏台已断开。请重新打开启动入口，然后刷新本页。');
  } finally {
    pollInFlight = false;
    pollTimer = setTimeout(
      poll,
      online && ['playing', 'countdown'].includes(state?.state) ? 40 : 400,
    );
  }
}

function lowerBound(events, t) {
  let a = 0,
    b = events.length;
  while (a < b) {
    const m = (a + b) >> 1;
    if (events[m].time < t) a = m + 1;
    else b = m;
  }
  return a;
}
function userPart() {
  return state?.part === 'accompaniment'
    ? 'melody'
    : state?.part === 'melody'
      ? 'accompaniment'
      : 'keys';
}
function renderUpcoming(pos) {
  if (!song) return;
  const part = userPart(),
    start = lowerBound(song.score, pos - 0.09),
    display = [];
  for (let i = start; i < song.score.length && display.length < 6; i++) {
    const e = song.score[i];
    if (e[part]) display.push(e);
  }
  const signature = part + ':' + display.map((e) => e.time).join(',');
  if (signature !== lastUpcoming) {
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < 6; i++) {
      const e = display[i],
        cell = document.createElement('div');
      cell.className = 'upcoming-cell';
      const keys = document.createElement('b'),
        time = document.createElement('small');
      keys.textContent = e ? (e[part].length > 1 ? '[' + e[part] + ']' : e[part]) : '·';
      time.textContent = e ? timeText(e.time, true) : '—';
      cell.append(keys, time);
      fragment.append(cell);
    }
    $('upcoming').replaceChildren(fragment);
    lastUpcoming = signature;
  }
  const active = new Set(),
    manual = new Set();
  if (state.state === 'playing') {
    for (
      let i = lowerBound(song.score, pos - 0.13);
      i < song.score.length && song.score[i].time <= pos + 0.015;
      i++
    ) {
      for (const key of song.score[i][state.part === 'all' ? 'keys' : state.part]) active.add(key);
      if (state.part !== 'all') for (const key of song.score[i][part]) manual.add(key);
    }
  }
  for (const [key, until] of manualKeys) {
    if (until > performance.now()) active.add(key);
    else manualKeys.delete(key);
  }
  document.querySelectorAll('.key').forEach((b) => {
    b.classList.toggle('active', active.has(b.dataset.key));
    b.classList.toggle('manual', manual.has(b.dataset.key));
  });
}
function drawTimeline() {
  const canvas = $('timeline'),
    width = canvas.clientWidth,
    height = 92,
    dpr = window.devicePixelRatio || 1;
  if (!width) return;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!song) return;
  const duration = song.duration,
    bins = Math.max(1, Math.floor(width / 4)),
    lead = new Float32Array(bins),
    back = new Float32Array(bins);
  for (const e of song.score) {
    const bin = Math.min(bins - 1, Math.floor((e.time / duration) * bins));
    lead[bin] += (e.melody || (!song.has_parts ? e.keys : '')).length;
    back[bin] += e.accompaniment.length;
  }
  const max = Math.max(1, ...lead, ...back);
  if (state?.loop.enabled) {
    ctx.fillStyle = '#dce7d0';
    ctx.fillRect(
      (state.loop.start / duration) * width,
      0,
      ((state.loop.end - state.loop.start) / duration) * width,
      height,
    );
  }
  for (let i = 0; i < bins; i++) {
    const x = (i * width) / bins;
    ctx.fillStyle = '#c9a164';
    ctx.fillRect(
      x,
      39 - Math.sqrt(lead[i] / max) * 28,
      2,
      Math.max(1, Math.sqrt(lead[i] / max) * 28),
    );
    ctx.fillStyle = '#77a28f';
    ctx.fillRect(x, 52, 2, Math.max(1, Math.sqrt(back[i] / max) * 28));
  }
  ctx.strokeStyle = '#d7dfcf';
  ctx.lineWidth = 1;
  for (const s of song.sections) {
    const x = (s.time / duration) * width;
    ctx.beginPath();
    ctx.moveTo(x, 3);
    ctx.lineTo(x, 89);
    ctx.stroke();
  }
  const x = (current().position / duration) * width;
  ctx.strokeStyle = '#326c5c';
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  ctx.moveTo(x, 0);
  ctx.lineTo(x, height);
  ctx.stroke();
}
function frame() {
  if (state && song) {
    const { position } = current();
    if (!seeking) {
      $('seek').value = position;
      $('currentTime').textContent = timeText(position, true);
    }
    const sec = [...song.sections].reverse().find((s) => s.time <= position);
    $('sectionName').textContent = sec?.name || '准备就绪';
    document
      .querySelectorAll('#sections button')
      .forEach((b) => b.classList.toggle('active', Number(b.dataset.time) === sec?.time));
    if (state.state === 'countdown') {
      $('stateBadge').textContent =
        state.output === 'game'
          ? `倒计时 ${Math.max(0, state.countdown_remaining - (performance.now() - anchor) / 1000).toFixed(1)} 秒`
          : '准备试听';
    }
    renderUpcoming(position);
  }
  if (performance.now() - lastDraw > 90) {
    drawTimeline();
    lastDraw = performance.now();
  }
  requestAnimationFrame(frame);
}

class Synth {
  constructor() {
    this.ctx = null;
    this.gain = null;
    this.buffers = new Map();
    this.nodes = new Set();
    this.nodeInfo = new Map();
    this.planned = new Map();
    this.seen = new Set();
    this.metronome = false;
    this.startAt = 0;
    this.startCycle = 0;
    this.firstBatch = true;
  }
  async unlock() {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.gain = this.ctx.createGain();
      this.gain.gain.value = (Number($('volume').value) / 100) * 0.7;
      this.gain.connect(this.ctx.destination);
    }
    if (this.ctx.state !== 'running') await this.ctx.resume();
  }
  buffer(key) {
    if (this.buffers.has(key)) return this.buffers.get(key);
    const sr = this.ctx.sampleRate,
      length = Math.ceil(sr * 2),
      buffer = this.ctx.createBuffer(1, length, sr),
      data = buffer.getChannelData(0),
      f = 440 * 2 ** ((pitch[key] - 69) / 12);
    for (let i = 0; i < length; i++) {
      const t = i / sr;
      let value = 0;
      for (const [h, a] of [
        [1, 1],
        [2, 0.34],
        [3, 0.15],
        [4, 0.075],
      ])
        value += a * Math.sin(2 * Math.PI * f * h * t) * Math.exp(-t * (1.9 + 0.75 * h));
      data[i] = value * Math.min(t / 0.005, 1) * Math.min((2 - t) / 0.06, 1) * 0.18;
    }
    this.buffers.set(key, buffer);
    return buffer;
  }
  note(key, when, identity = null) {
    if (!pitch[key] || !this.ctx) return;
    const node = this.ctx.createBufferSource();
    node.buffer = this.buffer(key);
    node.connect(this.gain);
    this.nodes.add(node);
    if (identity) this.nodeInfo.set(node, { when, identity });
    node.onended = () => {
      this.nodes.delete(node);
      this.nodeInfo.delete(node);
    };
    node.start(Math.max(this.ctx.currentTime, when));
  }
  click(when, strong) {
    const osc = this.ctx.createOscillator(),
      g = this.ctx.createGain();
    osc.frequency.value = strong ? 1250 : 880;
    g.gain.setValueAtTime(0.085, when);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 0.045);
    osc.connect(g);
    g.connect(this.gain);
    osc.start(when);
    osc.stop(when + 0.055);
    this.nodes.add(osc);
    osc.onended = () => this.nodes.delete(osc);
  }
  reset(position = state?.position || 0, cycle = state?.loop_count || 0) {
    for (const node of this.nodes) {
      try {
        node.stop();
      } catch {}
    }
    this.nodes.clear();
    this.nodeInfo.clear();
    this.planned.clear();
    this.seen.clear();
    this.startAt = position;
    this.startCycle = cycle;
    this.firstBatch = true;
  }
  changePart() {
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    // Retain sounding tails and the transport clock. Replace only future strikes.
    for (const [node, info] of this.nodeInfo) {
      if (info.when > now) {
        try {
          node.stop();
        } catch {}
        this.nodes.delete(node);
        this.nodeInfo.delete(node);
      }
    }
    for (const [identity, when] of this.planned) {
      if (when > now) {
        this.seen.delete(identity);
        this.planned.delete(identity);
      }
    }
  }
  schedule() {
    if (!online || !song || state?.state !== 'playing' || !this.ctx || this.ctx.state !== 'running')
      return;
    const playNotes = state.output === 'preview';
    if (!playNotes && !this.metronome) return;
    let { position, cycle } = current(),
      delay = 0,
      remaining = 0.2;
    for (let segment = 0; segment < 8 && remaining > 0; segment++) {
      const boundary = state.loop.enabled ? state.loop.end : song.duration;
      const span = Math.min(remaining, Math.max(0, (boundary - position) / state.speed));
      const end = position + span * state.speed;
      if (playNotes) {
        // Include the exact start/seek boundary even if the first status arrives late.
        // Never replay notes before the chosen position, or burst through a long delay.
        const floor =
          cycle === this.startCycle ? this.startAt : state.loop.enabled ? state.loop.start : 0;
        const earliest =
          this.firstBatch &&
          cycle === this.startCycle &&
          position - this.startAt < 0.3 * state.speed
            ? this.startAt
            : position - 0.07 * state.speed;
        for (
          let i = lowerBound(song.score, Math.max(floor, earliest) - 1e-8);
          i < song.score.length && song.score[i].time < end + 1e-7;
          i++
        ) {
          const e = song.score[i];
          if (state.loop.enabled && (e.time < state.loop.start || e.time >= state.loop.end))
            continue;
          const identity = `n${cycle}:${i}`;
          if (this.seen.has(identity)) continue;
          this.seen.add(identity);
          const when =
            this.ctx.currentTime + delay + Math.max(0, (e.time - position) / state.speed);
          this.planned.set(identity, when);
          for (const key of e[state.part === 'all' ? 'keys' : state.part])
            this.note(key, when, identity);
        }
      }
      if (this.metronome) {
        const beat = 60 / song.bpm;
        for (
          let n = Math.ceil((position - song.beat_origin - 0.035 * state.speed) / beat);
          song.beat_origin + n * beat < end;
          n++
        ) {
          const t = song.beat_origin + n * beat;
          if (t < 0 || (state.loop.enabled && t < state.loop.start)) continue;
          const identity = `m${cycle}:${n}`;
          if (this.seen.has(identity)) continue;
          this.seen.add(identity);
          this.click(
            this.ctx.currentTime + delay + Math.max(0, (t - position) / state.speed),
            n % song.beats_per_bar === 0,
          );
        }
      }
      remaining -= span;
      delay += span;
      this.firstBatch = false;
      if (!state.loop.enabled || end < boundary - 1e-7) break;
      position = state.loop.start;
      cycle++;
    }
    if (this.seen.size > 15000) {
      this.seen = new Set([...this.seen].slice(-5000));
      this.planned = new Map([...this.planned].filter(([id]) => this.seen.has(id)));
    }
  }
}
const synth = new Synth();
setInterval(() => synth.schedule(), 25);

// UI commands never send raw keys. Keyboard events here are local audio only.
$('search').addEventListener('input', renderLibrary);
$('refresh').addEventListener('click', () => refreshLibrary().then(() => toast('曲库已刷新')));
$('import').addEventListener('click', () => $('file').click());
$('file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 10 * 1024 * 1024) throw Error('曲谱不能超过 10 MB。');
    const data = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
    const added = await api('/api/import', { score: data });
    await refreshLibrary();
    await selectSong(added.id);
    toast('已加入曲库：' + added.title);
  } catch (error) {
    toast(error.message, true);
  } finally {
    e.target.value = '';
  }
});
$('play').addEventListener('click', async () => {
  if (!state) return;
  if (['playing', 'countdown'].includes(state.state)) await command('pause');
  else {
    if (state.output === 'preview' || synth.metronome) await synth.unlock();
    await command('start');
  }
});
$('stop').addEventListener('click', () => command('stop'));
$('quit').addEventListener('click', () => command('shutdown'));
for (const [id, delta] of [
  ['back', -5],
  ['forward', 5],
])
  $(id).addEventListener(
    'click',
    () =>
      state &&
      command('seek', {
        position: Math.max(0, Math.min(state.duration, current().position + delta)),
      }),
  );
for (const [id, delta] of [
  ['previous', -1],
  ['next', 1],
])
  $(id).addEventListener('click', () => command('song_step', { direction: delta }));
$('previousSection').addEventListener('click', () => command('section_step', { direction: -1 }));
$('nextSection').addEventListener('click', () => command('section_step', { direction: 1 }));
$('background').addEventListener('change', (e) => configure({ background: e.target.checked }));
document
  .querySelectorAll('[data-part]')
  .forEach((b) => b.addEventListener('click', () => configure({ part: b.dataset.part })));
document.querySelectorAll('[data-output]').forEach((b) =>
  b.addEventListener('click', async () => {
    if (b.dataset.output === 'preview') await synth.unlock();
    await configure({ output: b.dataset.output });
  }),
);
$('seek').addEventListener('input', () => {
  seeking = true;
  $('currentTime').textContent = timeText($('seek').value, true);
});
$('seek').addEventListener('change', async () => {
  const position = Number($('seek').value);
  await command('seek', { position });
  seeking = false;
});
$('timeline').addEventListener('click', (e) => {
  if (!song) return;
  const rect = e.currentTarget.getBoundingClientRect();
  command('seek', {
    position: Math.max(
      0,
      Math.min(song.duration, ((e.clientX - rect.left) / rect.width) * song.duration),
    ),
  });
});
$('timeline').addEventListener('keydown', (e) => {
  if (!state) return;
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault();
    command('seek', {
      position: Math.max(
        0,
        Math.min(state.duration, current().position + (e.key === 'ArrowRight' ? 5 : -5)),
      ),
    });
  }
});
$('speed').addEventListener('input', () => {
  speedEditing = true;
  showSpeed(Number($('speed').value) / 100);
});
$('speed').addEventListener('change', async () => {
  await configure({ speed: Number($('speed').value) / 100 });
  speedEditing = false;
  renderState();
});
document
  .querySelectorAll('[data-speed]')
  .forEach((b) => b.addEventListener('click', () => configure({ speed: Number(b.dataset.speed) })));
$('countdown').addEventListener('change', () =>
  configure({ countdown: Number($('countdown').value) }),
);
$('metronome').addEventListener('change', async (e) => {
  synth.metronome = e.target.checked;
  if (synth.metronome) await synth.unlock();
});
$('volume').addEventListener('input', () => {
  if (synth.gain)
    synth.gain.gain.setTargetAtTime(
      (Number($('volume').value) / 100) * 0.7,
      synth.ctx.currentTime,
      0.02,
    );
});
async function setLoop(values) {
  const result = await configure({ loop: values });
  if (result) {
    loopEditing = false;
    renderState();
  }
  return result;
}
async function applyLoop(enabled) {
  try {
    let start = parseTime($('loopA').value),
      end = parseTime($('loopB').value);
    if (Math.abs(end - state.duration) < 0.0011) end = state.duration;
    await setLoop({ enabled: enabled ?? $('loopEnabled').checked, start, end });
  } catch (error) {
    toast(error.message, true);
    renderState();
  }
}
$('loopA').addEventListener('input', () => (loopEditing = true));
$('loopB').addEventListener('input', () => (loopEditing = true));
$('applyLoop').addEventListener('click', () => applyLoop());
$('loopEnabled').addEventListener('change', () => applyLoop());
$('setA').addEventListener('click', () => {
  if (!state) return;
  const p = current().position;
  if (p >= state.loop.end - 0.25) {
    toast('起点需要早于终点至少 0.25 秒。', true);
    return;
  }
  setLoop({ start: p });
});
$('setB').addEventListener('click', () => {
  if (!state) return;
  const p = current().position;
  if (p <= state.loop.start + 0.25) {
    toast('终点需要晚于起点至少 0.25 秒。', true);
    return;
  }
  setLoop({ end: p });
});
$('loopSection').addEventListener('click', () => {
  if (!song) return;
  const pos = current().position;
  let i = song.sections.findIndex(
    (s, n) => s.time <= pos && (!song.sections[n + 1] || song.sections[n + 1].time > pos),
  );
  const start = i < 0 ? 0 : song.sections[i].time,
    end = song.sections[i + 1]?.time || song.duration;
  setLoop({ enabled: true, start, end });
});
for (const [name, keys] of [
  ['高音', 'QWERTYU'],
  ['中音', 'ASDFGHJ'],
  ['低音', 'ZXCVBNM'],
]) {
  const row = document.createElement('div');
  row.className = 'keyboard-row';
  const label = document.createElement('span');
  label.textContent = name;
  row.append(label);
  [...keys].forEach((key, i) => {
    const b = document.createElement('button');
    b.className = 'key';
    b.dataset.key = key;
    b.setAttribute('aria-label', `${name} ${i + 1}，${key} 键`);
    const letter = document.createElement('b');
    letter.textContent = key;
    const degree = document.createElement('small');
    degree.textContent = i + 1;
    b.append(letter, degree);
    b.addEventListener('pointerdown', () => manualNote(key));
    row.append(b);
  });
  $('keyboard').append(row);
}
async function manualNote(key) {
  await synth.unlock();
  synth.note(key, synth.ctx.currentTime);
  manualKeys.set(key, performance.now() + 180);
}
document.addEventListener('keydown', (e) => {
  if (e.repeat || e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
  // Native F5–F7 are registered by the Python input thread when available.
  // Preview-only mode handles them here, including suppressing F5 page reload.
  const part = { F5: 'all', F6: 'melody', F7: 'accompaniment' }[e.key];
  if (part && online && !state?.game_available) {
    e.preventDefault();
    configure({ part });
    return;
  }
  const navigation = {
    Insert: ['section_step', -1],
    Delete: ['section_step', 1],
    PageUp: ['song_step', -1],
    PageDown: ['song_step', 1],
  }[e.key];
  if (navigation && online && !state?.game_available) {
    e.preventDefault();
    command(navigation[0], { direction: navigation[1] });
    return;
  }
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target.tagName) || e.target.isContentEditable)
    return;
  const key = e.key.toUpperCase();
  if (pitch[key]) {
    e.preventDefault();
    manualNote(key);
  }
});
window.addEventListener('resize', drawTimeline);
refreshLibrary();
poll();
requestAnimationFrame(frame);
