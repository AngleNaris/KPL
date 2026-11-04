/* KPL 编辑器界面：表格与 LAYOUT 时间线共享同一份文档、选中项与未保存状态。 */
(function () {
  'use strict';

  const STATUS = {draft: '草稿', review: '审阅', final: '定稿'};
  const CONFIRM = {unconfirmed: '未确认', confirmed: '已确认'};
  const MARKS = [
    {key: 'done', label: '完成'}, {key: 'revise', label: '待修改'}, {key: 'wip', label: '进行中'},
    {key: 'question', label: '有疑问'}, {key: 'hold', label: '搁置'}
  ];
  // 标尺主刻度步长（秒）与细分数：取相邻主刻度不小于 TICK_GAP 像素的最小步长
  const TICK_STEPS = [[0.1, 5], [0.2, 4], [0.5, 5], [1, 5], [2, 4], [5, 5], [10, 5], [15, 3], [30, 6], [60, 6], [120, 4], [300, 5], [600, 6]];
  const TICK_GAP = 80;
  const MAX_PPS = 400;      // 最大缩放：每秒 400 像素，0.1 秒占 40 像素
  const UNKNOWN = '待确认';
  const DESCRIPTION_LIMIT = 300;  // 详细描述的显示字数上限，与 scripts/html_spec.py 一致
  const ASSET_DIR = '../05_参考资产/';
  const REF_PATTERN = /@\{([^{}]+)\}|<Picture \d+>/g; // 与 ShadowStage 的 @{…} 语法一致
  const DEFAULT_PREFS = {mode: 'dark', accent: 'amber'};
  const host = window.chrome && window.chrome.webview ? window.chrome.webview : null;

  /* 分段染色（格式见 KPL references/spec-format.md）：agent 用 {类别:内容} 标出分段，编辑器只显示内容并按类别染色。
     没有标记的文字不染色；分段内可以有 @{…} 引用。Agent 写入时一段一行（html_spec.py apply 强制）。 */
  const SEGMENT_TAGS = {镜头: 'camera', 风格: 'style', 场景: 'scene', 动态: 'action', 约束: 'constraint'};
  /* 视频提示词里的动态时段：{动态:0-4秒：内容}，时间精确到 0.1 秒（格式见 spec-format.md）。 */
  const BEAT_PATTERN = /^((?:0|[1-9]\d*)(?:\.\d)?)-((?:0|[1-9]\d*)(?:\.\d)?)秒：/;
  const SEGMENT_PATTERN = new RegExp(String.raw`\{(${Object.keys(SEGMENT_TAGS).join('|')})[:：]((?:@\{[^{}]*\}|[^{}])*)\}`, 'g');

  const SAVE_MESSAGES = {
    conflict: '文件已被其他程序或窗口修改；已停止覆盖，请保留当前修改并核对。',
    read_only: '文件为只读或没有写入权限；修改仍未保存。',
    write_unconfirmed: '写入结果未确认；请先检查原文件，不能直接重试。',
    busy: '另一个窗口正在保存，请稍后重试。',
    too_large: '规格内容过大，无法保证下次打开；修改仍未保存。',
    io_error: '文件不可用或正被其他程序占用；修改仍未保存。',
    locked: '已确认的版本不能在此修改；请另建草稿。',
    link_changed: '文件位置已变化；请核对后重新打开。',
    identity_changed: '文档身份与打开时不一致；修改仍未保存。',
    revision_changed: '修订号与打开时不一致；修改仍未保存。',
    invalid: '规格内容或版本无法核对；修改仍未保存。'
  };

  const $ = id => document.getElementById(id);
  const clone = value => JSON.parse(JSON.stringify(value));
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const saved = JSON.parse($('kpl-authoritative-data').textContent);
  const state = {
    doc: saved,
    working: clone(saved),
    selectedId: saved.shots.length ? saved.shots[0].id : null,
    timelineSel: null,   // 时间线上选中的自由轨条目 {track, id}
    view: 'table',
    editing: false,
    dirty: false,
    saving: false
  };

  function el(tag, props, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of [].concat(children || [])) if (child) node.append(child);
    return node;
  }

  function icon(name, extra) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon' + (extra ? ' ' + extra : ''));
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-' + name);
    svg.append(use);
    return svg;
  }

  /* 重新播放一次性的反馈动画（复制、设标记）。 */
  function pulse(node, cls) {
    if (!node) return;
    node.classList.remove(cls);
    void node.offsetWidth;
    node.classList.add(cls);
    node.addEventListener('animationend', () => node.classList.remove(cls), {once: true});
  }

  /* ---------- 数据读取：未知值一律“待确认”，不假设零或固定时长 ---------- */
  const shots = () => state.working.shots;
  const shotById = id => shots().find(shot => shot.id === id) || null;
  const filled = value => typeof value === 'string' && value.trim() !== '';
  const markOf = key => MARKS.find(mark => mark.key === key) || null;

  function seconds(shot) {
    const raw = (shot.duration_seconds || '').trim();
    if (!/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(raw)) return null;
    const value = Number(raw);
    return value > 0 ? value : null;
  }
  // 写入规格的时长只精确到 0.1 秒
  const DURATION_PATTERN = /^(?:\d+(?:\.\d)?|\.\d)$/;
  const tenth = value => { const n = Math.round(value * 10) / 10; return Number.isInteger(n) ? String(n) : n.toFixed(1); };

  /* 旧文件可能有 1.4166666666666667 这类时长：按累计切点取 0.1 秒整再相减（最短 0.1 秒），总长不漂移。
     返回改动的镜头数；空或无效的时长不动，交给“时长待确认”。 */
  function normalizeDurations(model) {
    let exact = 0, prev = 0, count = 0;
    for (const shot of model.shots) {
      const length = seconds(shot);
      if (length === null) continue;
      exact += length;
      const end = Math.max(Math.round(exact * 10) / 10, Math.round((prev + 0.1) * 10) / 10);
      const next = tenth(end - prev);
      prev = end;
      if (shot.duration_seconds.trim() !== next) { shot.duration_seconds = next; count++; }
    }
    return count;
  }
  const round = value => String(Math.round(value * 1000) / 1000);

  /* ---------- 动态时段：单位 0.1 秒；时段从 0 开始首尾相接，最后一段结束于镜头时长 ---------- */
  const tenths = text => Math.round(Number(text) * 10);
  const beatText = (from, to) => tenth(from / 10) + '-' + tenth(to / 10) + '秒：';

  /* 视频提示词里带时间的动态段，按出现顺序。 */
  function beatsOf(text) {
    const beats = [];
    for (const match of (text || '').matchAll(SEGMENT_PATTERN)) {
      const head = match[1] === '动态' ? match[2].match(BEAT_PATTERN) : null;
      if (head) beats.push({from: tenths(head[1]), to: tenths(head[2])});
    }
    return beats;
  }

  /* 按顺序把时段写回文字，其他内容不动。 */
  function withBeats(text, beats) {
    let index = 0;
    return text.replace(SEGMENT_PATTERN, (whole, tag, inner) => {
      const head = tag === '动态' ? inner.match(BEAT_PATTERN) : null;
      if (!head) return whole;
      const beat = beats[index++];
      return '{' + tag + ':' + beatText(beat.from, beat.to) + inner.slice(head[0].length) + '}';
    });
  }

  /* 改第 index 段的结束：下一段的开始跟着移动（每段至少 0.1 秒）；改的是最后一段时，镜头时长随之变化。返回实际的结束时间。 */
  function setBeatEnd(shot, index, to) {
    const beats = beatsOf(shot.video_prompt), beat = beats[index], next = beats[index + 1];
    to = Math.max(beat.from + 1, next ? Math.min(to, next.to - 1) : to);
    beat.to = to;
    if (next) next.from = to;
    else shot.duration_seconds = tenth(to / 10);
    shot.video_prompt = withBeats(shot.video_prompt, beats);
    return to;
  }

  /* 镜头时长在别处改了（表格、时间线、对齐视频等）时，最后一段的结束跟着改；
     时长不超过最后一段的开始就不动，交给时段块标警告。返回改了文字的镜头。 */
  const knownDurations = new Map();
  function followDurations() {
    const moved = [];
    for (const shot of shots()) {
      const total = seconds(shot) === null ? null : tenths(seconds(shot));
      if (knownDurations.get(shot.id) === total) continue;
      knownDurations.set(shot.id, total);
      const beats = beatsOf(shot.video_prompt), last = beats[beats.length - 1];
      if (!last || total === null || last.to === total || total <= last.from) continue;
      last.to = total;
      shot.video_prompt = withBeats(shot.video_prompt, beats);
      moved.push(shot);
    }
    return moved;
  }
  function seedDurations() {
    knownDurations.clear();
    for (const shot of shots()) knownDurations.set(shot.id, seconds(shot) === null ? null : tenths(seconds(shot)));
  }

  /* 时段块的位置条与警告：没有首尾相接、或最后一段没有结束在镜头时长时标警告色。 */
  function markBeats(box, shot) {
    const chips = [...box.querySelectorAll('.beat')], total = seconds(shot) === null ? null : tenths(seconds(shot));
    let prev = 0;
    chips.forEach((chip, index) => {
      const head = chip.dataset.beat.match(BEAT_PATTERN), from = tenths(head[1]), to = tenths(head[2]);
      const ok = from === prev && to > from && (index < chips.length - 1 || total === null || to === total);
      const span = Math.max(total || 0, to, 1);
      chip.classList.toggle('is-warn', !ok);
      chip.title = ok ? '' : '时段没有首尾相接，或最后一段没有结束在镜头时长';
      chip.style.setProperty('--from', from / span * 100 + '%');
      chip.style.setProperty('--to', to / span * 100 + '%');
      prev = to;
    });
  }

  /* 详情栏正显示的视频提示词跟着更新（输入中不重绘）。 */
  function refreshBeats(moved) {
    const box = $('f-video-prompt'), shot = shotById(state.selectedId);
    if (!box || !shot || !box.beatShot) return;
    if (moved.includes(shot) && document.activeElement !== box) box.replaceChildren(...richNodes(shot.video_prompt, !state.editing, shot));
    markBeats(box, shot);
  }
  const durationText = shot => seconds(shot) === null ? UNKNOWN : shot.duration_seconds.trim() + ' 秒';

  function plan() {
    let start = 0;
    const placed = [], pending = [];
    for (const shot of shots()) {
      const length = seconds(shot);
      if (length === null) { pending.push(shot); continue; }
      placed.push({shot, start, length});
      start += length;
    }
    return {placed, pending, total: start};
  }

  /* 时间线总长：镜头计划总长，与自由轨素材、字幕条目的最晚结束时间取大。
     多出来的部分只是素材占用的时间，不属于分镜表格，也不改变镜头时长。 */
  function span() {
    const planned = plan().total;
    const t = timeline();
    if (!t) return planned;
    let end = planned;
    for (const track of [...t.video.slice(1), ...t.audio]) {
      for (const clip of track.clips) end = Math.max(end, (clip.start_us + clip.out_us - clip.in_us) / US);
    }
    for (const track of t.subtitles) {
      for (const cue of track.cues) end = Math.max(end, cue.end_us / US);
    }
    for (const marker of t.markers) end = Math.max(end, (marker.end_us === null ? marker.start_us : marker.end_us) / US);
    return end;
  }

  /* ---------- 媒体时间线（v3 的 timeline）：文档里是整数微秒，界面按秒换算 ----------
     video[0] 是主轨：每个镜头最多关联一个视频，位置由镜头格决定；格子宽度始终是镜头时长，
     视频短于镜头时留空缺，长于镜头时多出的部分不用。 */
  const SCHEMA_V3 = 'kpl-user-spec-v3';
  const US = 1e6;
  const timeline = () => state.working.timeline || null;
  const mainClip = shot => (timeline() ? timeline().video[0].clips.find(clip => clip.shot_id === shot.id) : null) || null;
  const clipSeconds = clip => (clip.out_us - clip.in_us) / US;
  const baseName = source => source.split('/').pop();

  /* 第一次加入媒体时才建立时间线，保存时文档随之从 v2 升级为 v3。 */
  function ensureTimeline() {
    if (!state.working.timeline) {
      state.working.schema = SCHEMA_V3;
      state.working.timeline = {video: [{id: 'V1', name: '', muted: false, hidden: false, locked: false, gain: 1, clips: []}],
        audio: [], subtitles: [], markers: [], markers_locked: false};
    }
    return state.working.timeline;
  }

  function newTimelineId(prefix) {
    const t = ensureTimeline();
    const used = new Set([...t.video, ...t.audio, ...t.subtitles].flatMap(track =>
      [track.id, ...(track.clips || []).map(clip => clip.id), ...(track.cues || []).map(cue => cue.id)])
      .concat(t.markers.map(marker => marker.id)));
    let id;
    do id = prefix + Math.random().toString(36).slice(2, 10); while (used.has(id));
    return id;
  }

  /* ---------- 资产引用：@{id | 名称 | 文件名 | 主名 | 路径}，唯一匹配才算解析 ---------- */
  function aliases(asset) {
    const file = asset.path.split('/').pop();
    return [asset.id, asset.name, asset.path, file, file.replace(/\.[^.]+$/, '')];
  }

  function resolve(name) {
    const key = name.trim();
    const matches = state.working.assets.filter(asset => aliases(asset).includes(key));
    if (matches.length === 1) return {asset: matches[0]};
    return {issue: matches.length ? '对应多个资产，请改用 ID 或路径' : '资产表中没有这个资产'};
  }

  function inlineParts(text) {
    const parts = [];
    let last = 0;
    for (const match of text.matchAll(REF_PATTERN)) {
      if (match.index > last) parts.push({text: text.slice(last, match.index)});
      parts.push(match[1] === undefined ? {text: match[0], picture: true}
        : Object.assign({text: match[0], name: match[1]}, resolve(match[1])));
      last = match.index + match[0].length;
    }
    if (last < text.length) parts.push({text: text.slice(last)});
    return parts;
  }

  /* 切成片段：普通文字与引用，或 {tag, parts} 分段；分段标记本身不属于显示文字。 */
  function segments(text) {
    const items = [];
    let last = 0;
    text = text || '';
    for (const match of text.matchAll(SEGMENT_PATTERN)) {
      items.push(...inlineParts(text.slice(last, match.index)), {tag: match[1], parts: inlineParts(match[2])});
      last = match.index + match[0].length;
    }
    items.push(...inlineParts(text.slice(last)));
    return items;
  }

  function shotRefs(shot) {
    const assets = new Map(), issues = new Map();
    for (const part of [shot.description, shot.prompt, shot.video_prompt].flatMap(segments).flatMap(item => item.parts || [item])) {
      if (part.asset) assets.set(part.asset.id, part.asset);
      else if (part.issue) issues.set(part.text, part.issue);
    }
    return {assets: [...assets.values()], issues: [...issues.entries()]};
  }

  function imageUrl(path) {
    if (typeof path !== 'string' || !path.startsWith(ASSET_DIR)) return null;
    const rel = path.slice(ASSET_DIR.length).split('/').map(encodeURIComponent).join('/');
    return (host ? 'https://kpl-assets.local/' : 'preview-assets/') + rel;
  }

  /* 缩略图：原图单张可达数十 MB，界面一律只载入宿主生成的缩略图；没就绪的图不载入、只显示占位。
     预览环境没有宿主缓存，沿用原图路径。 */
  const thumbs = {map: new Map(), building: false};
  function thumbUrl(path) {
    if (!host) return imageUrl(path);
    const name = thumbs.map.get(path);
    return name ? 'https://kpl-thumbs.local/' + name : null;
  }

  /* ---------- 复制素材图片（只放位图） ---------- */
  async function copyAsset(asset, node) {
    pulse(node, 'copied');
    if (host) { host.postMessage({type: 'copyImage', path: asset.path}); status('正在复制 ' + asset.id + ' 的图片…'); return; }
    try {
      const blob = await (await fetch(imageUrl(asset.path))).blob();
      await navigator.clipboard.write([new ClipboardItem({[blob.type]: blob})]);
      status('已复制 ' + asset.id + ' ' + asset.name + ' 的图片。');
    } catch (_) { status('复制失败：浏览器没有允许写入剪贴板。'); }
  }

  /* ---------- 渲染：文本染色 ---------- */
  /* 只读时 @{…} 是可悬停、可点击复制的按钮；编辑时只是同样着色的文字。
     按钮用 span[role=button]：原生 button 不能跨行断开。以换行结尾时补一个占位换行，保持同样的高度。 */
  function partNode(part, interactive) {
    if (part.picture) return el('span', {class: 'tok tok-picture', text: part.text});
    if (part.asset && interactive) return el('span', {role: 'button', tabindex: '0', class: 'tok tok-ref', 'data-asset': part.asset.id,
      'aria-label': part.asset.id + ' ' + part.asset.name + '，复制图片', text: part.text});
    if (part.asset) return el('span', {class: 'tok tok-ref', text: part.text});
    if (part.issue) return el('span', {class: 'tok tok-missing', title: part.issue, text: part.text});
    return document.createTextNode(part.text);
  }

  /* beatShot 只在视频提示词里给出：动态段开头的时间显示成时段块。时段块不含文字节点（标签由 CSS 画出），
     光标偏移与保存都按它的原文（如“0-4秒：”）计算。 */
  function segNode(tag, parts, interactive, beatShot) {
    const nodes = parts.map(part => partNode(part, interactive));
    const first = parts[0], head = beatShot && tag === '动态' && first && first.name === undefined && !first.picture
      ? first.text.match(BEAT_PATTERN) : null;
    if (head) {
      nodes[0] = document.createTextNode(first.text.slice(head[0].length));
      nodes.unshift(el('span', {class: 'beat', contenteditable: 'false', 'data-beat': head[0],
        'data-label': tenth(tenths(head[1]) / 10) + '–' + tenth(tenths(head[2]) / 10) + 's'}));
    }
    return el('span', {class: 'seg seg-' + SEGMENT_TAGS[tag], 'data-tag': tag, title: tag}, nodes);
  }
  const isBeat = node => node.nodeType === Node.ELEMENT_NODE && node.dataset.beat !== undefined;
  const endBreak = () => el('br', {'data-end': ''});

  function richNodes(text, interactive, beatShot) {
    const nodes = segments(text).map(item => item.parts ? segNode(item.tag, item.parts, interactive, beatShot) : partNode(item, interactive));
    if ((text || '').endsWith('\n')) nodes.push(endBreak());
    return nodes;
  }

  /* 把编辑区还原成带分段标记的文字；内容被删空的分段直接去掉。 */
  const childText = node => [...node.childNodes].map(nodeText).join('');
  function nodeText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.data;
    if (isBeat(node)) return node.dataset.beat;
    if (node.nodeName === 'BR') return node.hasAttribute('data-end') ? '' : '\n';
    const inner = childText(node);
    if (!node.dataset.tag) return inner;
    return inner ? '{' + node.dataset.tag + ':' + inner + '}' : '';
  }

  /* 引用、空分段与实际文字不一致时才重绘，平时保留浏览器的撤销记录。 */
  function stale(box) {
    const refs = [...box.textContent.matchAll(REF_PATTERN)].map(match => match[0]).join('\0');
    const toks = [...box.querySelectorAll('.tok')].map(node => node.textContent).join('\0');
    return refs !== toks || box.querySelector('.seg:empty') !== null || (box.firstChild !== null && !childText(box).trim());
  }

  /* 编辑中只重新识别引用：分段以编辑区里现有的 span 为准，不从文字重新解析，
     这样输入到一半的 @{ 不会让整段失去分段。 */
  function retokenize(box) {
    const at = caretOffset(box), text = childText(box), nodes = [];
    let run = '';
    const flush = () => { nodes.push(...inlineParts(run).map(part => partNode(part, false))); run = ''; };
    if (text.trim()) {
      for (const child of box.childNodes) {
        if (!child.dataset || !child.dataset.tag) { run += nodeText(child); continue; }
        flush();
        const inner = childText(child);
        if (inner) nodes.push(segNode(child.dataset.tag, inlineParts(inner), false, box.beatShot));
      }
      flush();
      if (text.endsWith('\n')) nodes.push(endBreak());
    }
    box.replaceChildren(...nodes);
    if (at !== null) setCaret(box, at);
  }

  /* 光标与选区按可见文字的偏移记录，重绘后再按偏移放回。时段块按原文计入，与保存的文字一一对应。 */
  const plainOf = node => node.nodeType === Node.TEXT_NODE ? node.data : isBeat(node) ? node.dataset.beat
    : [...node.childNodes].map(plainOf).join('');
  function textOffset(root, node, at) {
    const range = document.createRange();
    range.selectNodeContents(root);
    range.setEnd(node, at);
    return plainOf(range.cloneContents()).length;
  }

  function pointAt(root, offset) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const length = node.nodeType === Node.TEXT_NODE ? node.data.length : isBeat(node) ? node.dataset.beat.length : -1;
      if (length < 0) continue;
      if (offset <= length && node.nodeType === Node.TEXT_NODE) return [node, offset];
      // 落在时段块上时放到块的前面或后面
      if (offset < length || (offset === length && !node.nextSibling)) {
        return [node.parentNode, [...node.parentNode.childNodes].indexOf(node) + (offset ? 1 : 0)];
      }
      offset -= length;
    }
    return [root, root.childNodes.length];
  }

  function caretOffset(root) {
    const selection = getSelection();
    if (!selection.rangeCount || !root.contains(selection.focusNode)) return null;
    return textOffset(root, selection.focusNode, selection.focusOffset);
  }

  const setCaret = (root, offset) => getSelection().collapse(...pointAt(root, offset));
  const selectSpan = (root, [start, end]) => getSelection().setBaseAndExtent(...pointAt(root, start), ...pointAt(root, end));

  function selectionSpan(root) {
    const selection = getSelection();
    if (!selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
    return [textOffset(root, range.startContainer, range.startOffset), textOffset(root, range.endContainer, range.endOffset)];
  }

  /* 编辑区的可见文字，以及每个字符所属的分段类别（null 为无类别）。 */
  function charTags(box) {
    let text = '';
    const tags = [];
    const walk = (node, inherited) => {
      for (const child of node.childNodes) {
        if (child.nodeType !== Node.TEXT_NODE && child.nodeName !== 'BR' && !isBeat(child)) { walk(child, child.dataset.tag || inherited); continue; }
        const part = nodeText(child);
        text += part;
        tags.push(...Array(part.length).fill(inherited));
      }
    };
    walk(box, null);
    return {text, tags};
  }

  /* 把选中的文字设为一个分段（tag 为 null 时清除类别）：跨过的旧分段被切开，引用不拆开。返回新文字与实际范围。 */
  function retag(box, [start, end], tag) {
    const {text, tags} = charTags(box);
    for (const match of text.matchAll(REF_PATTERN)) {
      const from = match.index, to = from + match[0].length;
      if (from < end && to > start) { start = Math.min(start, from); end = Math.max(end, to); }
    }
    tags.fill(tag, start, end);
    let out = '';
    for (let i = 0, j; i < text.length; i = j) {
      for (j = i; j < text.length && tags[j] === tags[i]; j++);
      out += tags[i] ? '{' + tags[i] + ':' + text.slice(i, j) + '}' : text.slice(i, j);
    }
    return {text: out, span: [start, end]};
  }

  /* 只读与编辑共用同一个元素，解锁只加 contenteditable，布局不变。分段标记不显示，编辑时留在所在分段上。
     输入法组字期间不重绘，组字结束再处理。 */
  function textBlock(id, title, shot, key, onChange, limit) {
    const editing = state.editing, beatShot = key === 'video_prompt' ? shot : null;
    const box = el('div', {id, class: 'rich', role: 'textbox', 'aria-multiline': 'true', 'aria-labelledby': id + '-title',
      'aria-readonly': editing ? null : 'true', contenteditable: editing ? 'plaintext-only' : null, spellcheck: 'false'},
      richNodes(shot[key], !editing, beatShot));
    box.beatShot = beatShot;
    if (beatShot) {
      markBeats(box, shot);
      box.addEventListener('click', event => {
        const chip = event.target.closest('.beat');
        if (chip && state.editing) openBeat(chip, box, shot);
      });
    }
    // 有上限的字段在标题旁显示显示字数（不计分段标记）；超出只标警告色，不拦保存，上限由 agent 端检查执行
    const count = limit ? el('span', {class: 'count'}) : null;
    const recount = () => {
      if (!count) return;
      const length = [...shot[key].replace(SEGMENT_PATTERN, '$2')].length;
      count.textContent = length + '/' + limit;
      count.classList.toggle('is-over', length > limit);
      count.title = length > limit ? '超出 ' + limit + ' 字：这里只概括画面内容' : '';
    };
    recount();
    const update = event => {
      if (event.isComposing) return;
      if (stale(box)) retokenize(box);
      shot[key] = childText(box);
      if (beatShot) markBeats(box, shot);
      recount();
      onChange();
    };
    box.addEventListener('input', update);
    box.addEventListener('compositionend', update);
    // 解锁后选中文字再右键：指定这段的类别。没有选中文字时保留系统菜单。
    box.addEventListener('contextmenu', event => {
      const span = state.editing ? selectionSpan(box) : null;
      if (!span) return;
      event.preventDefault();
      const rects = getSelection().getRangeAt(0).getClientRects();
      const anchor = rects.length ? rects[rects.length - 1] : box.getBoundingClientRect();
      const selected = new Set(charTags(box).tags.slice(...span));
      const current = selected.size === 1 ? [...selected][0] : null;
      openMenu({
        label: '分段类别', anchor: {getBoundingClientRect: () => anchor}, align: 'start',
        restore: () => { box.focus(); selectSpan(box, span); },
        items: [...Object.keys(SEGMENT_TAGS), null].map(tag => ({
          label: tag || '清除类别', checked: tag !== null && current === tag,
          icon: el('span', {class: 'seg-swatch ' + (tag ? 'seg-' + SEGMENT_TAGS[tag] : 'is-none')}),
          pick: () => {
            const result = retag(box, span, tag);
            box.replaceChildren(...richNodes(result.text, false, beatShot));
            if (beatShot) markBeats(box, shot);
            shot[key] = result.text;
            recount();
            box.focus(); selectSpan(box, result.span);
            onChange();
          }
        }))
      });
    });
    const copy = COPYABLE.includes(key) ? el('button', {type: 'button', class: 'btn btn-quiet btn-icon block-copy', title: '复制' + title,
      'aria-label': '复制' + title, onclick: event => copyPrompt(shot[key], event.currentTarget)}, icon('copy')) : null;
    return el('section', {class: 'block'}, [el('h3', {class: 'block-title'}, [el('span', {id: id + '-title', text: title}), count, copy]), box]);
  }

  /* 时段块的时间弹层：改开始（即上一段的结束）或结束，回车或离开输入框生效，上下键按 0.1 秒调。 */
  let beatEdit = null;
  function openBeat(chip, box, shot) {
    const index = [...box.querySelectorAll('.beat')].indexOf(chip);
    if (!beatsOf(shot.video_prompt)[index]) return;
    beatEdit = {box, shot, index};
    fillBeat();
    const pop = $('beat-pop');
    pop.hidden = false;
    place(pop, chip, 'start');
    $('beat-to').focus(); $('beat-to').select();
  }

  function fillBeat() {
    const {shot, index} = beatEdit, beat = beatsOf(shot.video_prompt)[index];
    $('beat-from').value = tenth(beat.from / 10);
    $('beat-from').disabled = index === 0;
    $('beat-to').value = tenth(beat.to / 10);
  }

  function closeBeat(restore) {
    const pop = $('beat-pop');
    if (pop.hidden) return;
    pop.hidden = true;
    if (restore && beatEdit) beatEdit.box.focus();
    beatEdit = null;
  }

  function commitBeat(edge) {
    if (!beatEdit) return;
    const {box, shot, index} = beatEdit, value = Number($(edge === 'from' ? 'beat-from' : 'beat-to').value);
    if (!Number.isFinite(value) || (edge === 'from' && index === 0)) { fillBeat(); return; }
    const before = shot.duration_seconds;
    setBeatEnd(shot, edge === 'from' ? index - 1 : index, Math.round(value * 10));
    box.replaceChildren(...richNodes(shot.video_prompt, false, shot));
    markBeats(box, shot);
    changed(); renderTable();
    fillBeat();
    place($('beat-pop'), box.querySelectorAll('.beat')[index], 'start');
    if (shot.duration_seconds !== before) status(shot.number + ' 时长改为 ' + shot.duration_seconds + ' 秒。');
  }

  function setupBeatPop() {
    const pop = $('beat-pop');
    for (const [id, edge] of [['beat-from', 'from'], ['beat-to', 'to']]) {
      $(id).addEventListener('change', () => commitBeat(edge));
      $(id).addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); commitBeat(edge); closeBeat(true); }
        else if (event.key === 'Escape') { event.preventDefault(); closeBeat(true); }
      });
    }
    pop.addEventListener('focusout', event => { if (!pop.contains(event.relatedTarget)) closeBeat(false); });
    document.addEventListener('pointerdown', event => {
      if (!pop.hidden && !pop.contains(event.target) && !event.target.closest('.beat')) closeBeat(false);
    });
  }

  /* 一键复制提示词：去掉分段标记、保留换行，引用换成前后带空格的“@资产名”，方便在生成平台上手动设置引用的资产。
     找不到的引用照原名写成“@名字”；<Picture N> 原样保留。 */
  const COPYABLE = ['prompt', 'video_prompt'];
  function promptText(text) {
    return (text || '').replace(SEGMENT_PATTERN, '$2')
      .replace(REF_PATTERN, (match, name) => {
        if (name === undefined) return match;
        const {asset} = resolve(name);
        return ' @' + (asset ? asset.name : name.trim()) + ' ';
      })
      .split('\n').map(line => line.replace(/ {2,}/g, ' ').trim()).join('\n');
  }

  async function copyPrompt(text, node) {
    if (!(text || '').trim()) { status('提示词是空的，没有可复制的内容。'); return; }
    pulse(node, 'copied');
    if (host) { host.postMessage({type: 'copyText', text: promptText(text)}); return; }
    try { await navigator.clipboard.writeText(promptText(text)); status('已复制提示词。'); }
    catch (_) { status('复制失败：浏览器没有允许写入剪贴板。'); }
  }

  /* ---------- 渲染：外框 ---------- */
  function renderChrome() {
    const doc = state.working;
    $('doc-title').textContent = doc.title || '未命名规格';
    document.title = (state.dirty ? '* ' : '') + (doc.title || 'KPL 规格');
    $('doc-meta').textContent = [STATUS[doc.status], CONFIRM[doc.confirmation], doc.version, '修订 ' + doc.revision].join(' · ');
    $('dirty-mark').hidden = !state.dirty;

    const toggle = $('edit-toggle');
    toggle.replaceChildren(icon(state.editing ? 'lock' : 'unlock'), el('span', {text: state.editing ? '退出编辑' : '解锁编辑'}));
    toggle.disabled = !canEdit() || state.saving;
    $('save').disabled = !state.editing || !state.dirty || state.saving;

    const {pending, total} = plan();
    $('facts').textContent = shots().length + ' 镜 · 总长 ' + round(total) + ' 秒' +
      (pending.length ? ' · ' + pending.length + ' 镜时长待确认' : '');
  }

  /* ---------- 渲染：表格 ---------- */
  function thumb(shot) {
    const url = thumbUrl(shot.thumbnail);
    // 路径有效但缓存未就绪时留下 data-thumb-shot，缩略图生成后由 applyThumbs 原地替换
    const empty = () => el('span', {class: 'thumb thumb-empty', role: 'img', 'aria-label': shot.number + ' 暂无画面', 'data-thumb-shot': imageUrl(shot.thumbnail) ? shot.id : null}, icon('image'));
    if (!url) return empty();
    const img = el('img', {class: 'thumb', src: url, alt: shot.number + ' 参考画面', loading: 'lazy', 'data-peek-shot': shot.id});
    img.addEventListener('error', () => img.replaceWith(empty()));
    return img;
  }

  function markIcon(shot) {
    const mark = markOf(shot.mark);
    return mark
      ? el('span', {class: 'mark mark-' + mark.key, role: 'img', 'aria-label': '标记：' + mark.label, title: mark.label}, icon('mark-' + mark.key))
      : el('span', {class: 'mark mark-none'});
  }

  function assetsCell(shot) {
    const {assets, issues} = shotRefs(shot);
    // 警告放在数字前，保证各行数字右对齐
    return [
      issues.length ? el('span', {class: 'ref-alert', role: 'img', 'aria-label': issues.length + ' 个引用未找到', title: issues.length + ' 个引用未找到'}, icon('alert')) : null,
      el('span', {class: assets.length ? '' : 'field-empty', text: assets.length + ' 项'})
    ].filter(Boolean);
  }

  /* 单元格始终是同一个无边框输入框：只读时 readonly，解锁后可编辑，外观与布局不变。
     只读时整行只保留镜号一个 Tab 停靠点。 */
  function cellInput(shot, key, label, cls, tabbable) {
    const multiline = key === 'content';
    const input = el(multiline ? 'textarea' : 'input', {class: 'cell-input ' + cls, 'aria-label': shot.number + ' ' + label,
      type: multiline ? null : 'text', rows: multiline ? '1' : null, placeholder: UNKNOWN, autocomplete: 'off', spellcheck: 'false',
      readonly: !state.editing, tabindex: state.editing || tabbable ? '0' : '-1',
      'aria-invalid': key === 'duration_seconds' && filled(shot[key]) && !DURATION_PATTERN.test(shot[key].trim()) ? 'true' : null});
    input.value = shot[key] || '';
    if (key === 'duration_seconds') {
      // 输入过程中的半截数字不联动，改完离开单元格时再按改动前后的镜头位置平移其他轨道
      let link = null;
      input.addEventListener('focus', () => { link = state.editing ? linkSnapshot() : null; });
      input.addEventListener('change', () => { if (link) { applyLink(link); link = linkSnapshot(); changed(); } });
    }
    input.addEventListener('input', () => {
      shot[key] = input.value;
      if (key === 'duration_seconds') {
        input.setAttribute('aria-invalid', String(filled(input.value) && (seconds(shot) === null || !DURATION_PATTERN.test(input.value.trim()))));
        input.nextElementSibling.hidden = !filled(input.value);
      }
      if (key === 'number' && shot.id === state.selectedId) $('detail-title').textContent = input.value;
      changed();
    });
    return input;
  }

  function markButton(shot) {
    const mark = markOf(shot.mark);
    return el('button', {type: 'button', class: 'mark-btn', 'aria-haspopup': state.editing ? 'menu' : null,
      'aria-expanded': state.editing ? 'false' : null, 'aria-disabled': state.editing ? null : 'true', tabindex: state.editing ? null : '-1',
      'aria-label': shot.number + ' 标记：' + (mark ? mark.label : '未标记') + (state.editing ? '，更改' : ''),
      onclick: event => { if (state.editing) openMarkMenu(event.currentTarget, shot); }}, markIcon(shot));
  }

  function renderTable() {
    const body = $('shot-rows');
    if (!shots().length) {
      body.replaceChildren(el('tr', {}, el('td', {class: 'empty', colspan: '8', text: '这份规格还没有镜头。'})));
      return;
    }
    body.replaceChildren(...shots().map(shot => {
      const selected = shot.id === state.selectedId;
      return el('tr', {'data-id': shot.id, 'aria-selected': String(selected), onclick: () => select(shot.id)}, [
        el('td', {class: 'td-thumb'}, thumb(shot)),
        el('td', {}, cellInput(shot, 'number', '镜号', 'cell-number mono', selected)),
        el('td', {class: 'num'}, [cellInput(shot, 'duration_seconds', '时长（秒）', 'cell-dur mono'),
          el('span', {class: 'unit', text: ' 秒', hidden: !filled(shot.duration_seconds)})]),
        el('td', {}, cellInput(shot, 'content', '一句话内容', '')),
        el('td', {}, cellInput(shot, 'shot_scale', '景别', '')),
        el('td', {}, cellInput(shot, 'camera_motion', '镜头运动', '')),
        el('td', {class: 'num td-num td-assets'}, assetsCell(shot)),
        el('td', {class: 'center'}, markButton(shot))
      ]);
    }));
  }

  /* ---------- 渲染：时间线（解锁后可像剪辑软件一样拖动顺序与时长） ---------- */
  const PLACE_SECONDS = 2;   // 从待定区拖入轨道时的起始时长，之后拖右边缘调整
  const snap = value => Math.max(0.1, Math.round(value * 10) / 10);
  let suppressClick = false;

  /* 片段：参考画面沿片段横向平铺成胶片条，顶部标签写镜号、时长与标记。 */
  function clip(shot, onTrack) {
    const selected = shot.id === state.selectedId, url = thumbUrl(shot.thumbnail);
    const strip = url ? el('span', {class: 'clip-strip'}) : null;
    if (strip) strip.style.backgroundImage = 'url("' + url + '")';
    return el('button', {type: 'button', class: 'clip', 'data-id': shot.id, 'aria-pressed': String(selected),
      tabindex: selected ? '0' : '-1', 'aria-label': shot.number + '，' + durationText(shot),
      'aria-keyshortcuts': state.editing && onTrack ? 'Alt+ArrowLeft Alt+ArrowRight Shift+ArrowLeft Shift+ArrowRight' : null,
      onclick: () => { if (!suppressClick) select(shot.id); }}, [
      strip,
      el('span', {class: 'clip-label'}, [
        el('span', {class: 'clip-id', text: shot.number}),
        el('span', {class: 'clip-dur', text: durationText(shot).replace(' ', '')}),
        versionBadge(shot),
        markOf(shot.mark) ? markIcon(shot) : null
      ]),
      onTrack ? videoCover(shot) : null,
      state.editing && onTrack && !mainLocked() ? el('span', {class: 'clip-handle is-start', 'data-edge': 'start',
        title: '拖动移动与上一镜的切点；按住 Alt 只改本镜时长'}) : null,
      state.editing && onTrack && !mainLocked() ? el('span', {class: 'clip-handle', 'data-edge': 'end', title: '拖动调整时长'}) : null
    ]);
  }

  /* 有多个视频版本时，在镜头标签里标出“当前是第几个 / 共几个”。 */
  function versionBadge(shot) {
    const clip = mainClip(shot);
    if (!clip || !clip.takes.length) return null;
    const versions = versionsOf(clip);
    return el('span', {class: 'clip-take', title: '视频版本：' + baseName(clip.source),
      text: (versions.findIndex(version => version.id === clip.id) + 1) + '/' + versions.length});
  }

  /* 片段底部的覆盖条：实色是视频占用的部分，斜纹是视频短于镜头留下的空缺；素材不可用时整条警告色。 */
  function videoCover(shot) {
    const clip = mainClip(shot), length = seconds(shot);
    if (!clip || length === null) return null;
    const problem = mediaProblem(clip.source);
    const node = el('span', {class: 'clip-video' + (problem ? ' is-missing' : ''), title: problem || null}, el('i'));
    node.style.setProperty('--used', Math.min(1, clipSeconds(clip) / length) * 100 + '%');
    return node;
  }

  function ticks(total, step) {
    const values = [];
    for (let index = 0; index * step <= total + 1e-9; index++) values.push({at: index * step, label: true});
    const gap = total - values[values.length - 1].at;
    // 终点总要有刻度；离上一刻度太近时只画线，不写数字以免重叠。
    if (gap > 1e-9) values.push({at: total, label: gap > step / 2});
    return values;
  }

  /* ---------- 缩放：比例尺为每秒像素数；null 表示适配全部，整条时间线正好铺满可见宽度 ---------- */
  const zoom = {pps: null};
  const layout = {pps: 0, gutter: 0};  // 最近一次排布的每秒像素数与左侧抬头宽度；播放头、跳转与拖动都按它们换算

  function fitPps() {
    const total = span(), width = $('timeline-scroll').clientWidth - layout.gutter;
    // 视图隐藏或窄于左侧抬头时宽度不为正，不能拿来算比例尺，否则会得到负坐标
    return total > 0 && width > 0 ? width / total : 0;
  }
  /* 时间（秒）→ 时间线内的横坐标；泳道内部从 0 起算，跨列的播放头与标记要加抬头宽度。 */
  const timeX = seconds => seconds * layout.pps;
  const trackX = seconds => layout.gutter + seconds * layout.pps;
  const currentPps = () => zoom.pps || fitPps();

  function renderTimeline() {
    const {placed, pending, total} = plan();
    $('timeline').classList.toggle('is-editing', state.editing);
    // 解锁后即使还没有排布的镜头也保留轨道，作为待定镜头的落点
    renderLanes();
    // 泳道重建后再填主轨片段（主轨抬头也由 renderLanes 生成，会整条替换）
    $('track').hidden = total === 0 && !state.editing;
    $('track').replaceChildren(...placed.map(item => clip(item.shot, true)));
    $('pending-lane').hidden = pending.length === 0;
    $('pending-list').replaceChildren(...pending.map(shot => clip(shot, false)));
    renderMarkers();
    $('marker-add').setAttribute('aria-disabled', state.editing ? 'false' : 'true');
    layoutTimeline(currentPps());
    renderMonitor();
  }

  /* 轨道自上而下：字幕轨（始终在最上，序号小的更靠上）→ 自由视频轨（序号大的更靠上，覆盖下层）→ 主轨 V1 → 音频轨。 */
  const mainLane = () => $('lanes').querySelector('.lane-main');

  function renderLanes() {
    const t = timeline(), lanes = $('lanes'), track = $('track');
    if (!t) {
      const main = mainLane();
      main.replaceChildren(el('div', {class: 'lane-body'}, track));
      lanes.replaceChildren(main);
      return;
    }
    const rows = [
      ...t.subtitles.map((track, index) => ({kind: 'subtitle', track, code: 'S' + (index + 1)})),
      ...t.video.slice(1).map((track, index) => ({kind: 'video', track, code: 'V' + (index + 2)})).reverse(),
      ...t.audio.map((track, index) => ({kind: 'audio', track, code: 'A' + (index + 1)}))
    ];
    const nodes = rows.map(row => laneNode(row));
    const fresh = [...nodes.filter(row => row.dataset.kind !== 'audio'), laneNode({kind: 'video', track: t.video[0], code: 'V1', main: true}),
      ...nodes.filter(row => row.dataset.kind === 'audio')];
    // 已有的轨道就地更新（过渡能在原节点上播放、焦点不丢），新轨道淡入，去掉的直接移除
    const key = node => node.dataset.kind + ':' + node.dataset.id;
    const old = new Map([...lanes.children].filter(node => node.dataset.kind).map(node => [key(node), node]));
    const ordered = fresh.map(node => {
      const prev = old.get(key(node));
      if (!prev) return node;
      old.delete(key(node));
      syncAttributes(prev, node);
      morph(prev.querySelector('.lane-head'), node.querySelector('.lane-head'));
      if (!node.classList.contains('lane-main')) syncKeyed(prev.querySelector('.lane-body'), [...node.querySelector('.lane-body').children], 'data-id');
      return prev;
    });
    for (const node of [...lanes.children]) if (!ordered.includes(node)) node.remove();
    ordered.forEach((node, index) => { if (lanes.children[index] !== node) lanes.insertBefore(node, lanes.children[index] || null); });
    // 主轨的泳道里放的是页面原有的 #track
    const mainBody = lanes.querySelector('.lane-main .lane-body');
    if (mainBody.firstElementChild !== track) mainBody.replaceChildren(track);
    // 新建的条目还没有位置；沿用最近一次比例尺立刻排布，不等下一次 layoutTimeline
    if (layout.pps > 0) layoutLanes(layout.pps);
    markLaneSelection();
  }

  function syncAttributes(old, fresh) {
    for (const {name} of [...old.attributes]) if (!fresh.hasAttribute(name)) old.removeAttribute(name);
    for (const {name, value} of [...fresh.attributes]) if (old.getAttribute(name) !== value) old.setAttribute(name, value);
  }

  /* 把 fresh 的属性与子节点同步到 old 上，尽量保留原节点。只用于没有绑定事件闭包的节点（轨道抬头按钮用委托处理）；
     标签不同时直接换成新节点。 */
  function morph(old, fresh) {
    syncAttributes(old, fresh);
    const before = [...old.childNodes], after = [...fresh.childNodes];
    after.forEach((child, index) => {
      const prev = before[index];
      if (prev && prev.nodeType === 1 && child.nodeType === 1 && prev.tagName === child.tagName) morph(prev, child);
      else if (prev && prev.nodeType === 3 && child.nodeType === 3) { if (prev.data !== child.data) prev.data = child.data; }
      else if (prev) old.replaceChild(child, prev);
      else old.append(child);
    });
    for (const extra of before.slice(after.length)) extra.remove();
  }

  /* 按 key 复用容器里的子节点：留下的就地同步，新来的淡入（@starting-style），去掉的淡出后再移除。 */
  let leaveGone = false;   // 载入外部修改时为真：删除的条目留警告色残影，淡出得慢一些
  function syncKeyed(container, fresh, key) {
    const old = new Map([...container.children].filter(node => !node.classList.contains('is-leaving') && !node.classList.contains('is-gone'))
      .map(node => [node.getAttribute(key), node]));
    for (const node of fresh) {
      const prev = old.get(node.getAttribute(key));
      if (prev) { morph(prev, node); old.delete(node.getAttribute(key)); } else container.append(node);
    }
    for (const node of old.values()) {
      node.classList.add(leaveGone ? 'is-gone' : 'is-leaving');
      setTimeout(() => node.remove(), reducedMotion() ? 0 : leaveGone ? GONE_MS : 200);
    }
  }

  /* 一条轨道：左侧抬头写轨道编号、开关、锁与菜单，右侧泳道里放片段。主轨的泳道由 renderLanes 放入页面原有的 #track。
     抬头按钮不绑闭包，点击由 #lanes 上的委托处理（setupLaneHeads），这样重绘时可以复用节点。 */
  function laneNode({kind, track, code, main}) {
    const label = code;   // 轨道只显示编号，不显示名称
    // 开关写在文档里，与片段的声音开关一样只在解锁后可改；只读时照样显示状态。按下（aria-pressed）表示已关闭
    const toggle = (field, name, offIcon, onIcon) => el('button', {type: 'button', class: 'btn btn-quiet btn-icon lane-mute',
      title: (track[field] ? '打开' : '关闭') + name, 'aria-pressed': String(track[field]), 'aria-label': label + ' 关闭' + name,
      'aria-disabled': state.editing ? null : 'true', 'data-act': field}, [icon(track[field] ? offIcon : onIcon)]);
    const picture = kind !== 'audio' ? toggle('hidden', '画面', 'eye-off', 'eye') : null;
    const sound = kind !== 'subtitle' ? toggle('muted', '声音', 'mute', 'volume') : null;
    const off = kind === 'audio' ? track.muted : track.hidden;
    const lock = el('button', {type: 'button', class: 'btn btn-quiet btn-icon lane-mute lane-lock',
      title: track.locked ? '解锁这一行' : '锁定这一行', 'aria-pressed': String(track.locked), 'aria-label': label + ' 锁定',
      'aria-disabled': state.editing ? null : 'true', 'data-act': 'locked'}, [icon(track.locked ? 'lock' : 'unlock')]);
    // 轨道操作都要解锁后才能用；只读时变灰，不弹出空菜单
    const more = el('button', {type: 'button', class: 'btn btn-quiet btn-icon lane-mute', title: '轨道操作', 'aria-haspopup': 'menu',
      'aria-expanded': 'false', 'aria-label': label + ' 轨道操作', 'aria-disabled': state.editing ? null : 'true', 'data-act': 'more'},
      [icon('more')]);
    const body = el('div', {class: 'lane-body'});
    if (!main && kind === 'subtitle') body.append(...track.cues.map(cue => cueNode(cue, track)));
    else if (!main) for (const clip of track.clips) body.append(mediaClipNode(clip, track));
    return el('div', {class: 'lane lane-' + kind + (main ? ' lane-main' : '') + (track.locked ? ' is-locked' : ''),
      'data-kind': kind, 'data-id': track.id},
      [el('div', {class: 'lane-head'}, [
        el('span', {class: 'lane-name' + (off ? ' is-off' : ''), text: label, title: label}), picture, sound, lock, more
      ]), body]);
  }

  function toggleLock(track, label) {
    if (!state.editing) return;
    editLane(track.locked ? label + ' 已解锁。' : label + ' 已锁定：不能拖动、裁剪或删除其中的内容，联动也不移动它。', () => {
      track.locked = !track.locked;
    });
  }

  /* 轨道操作：清空本轨（片段与字幕条目都在轨道里），或把整条轨从时间线上移除。主轨 V1 不能移除。 */
  function openTrackActions(trigger, kind, track, label) {
    openMenu({trigger, align: 'start', label: label + ' 轨道操作', restore: () => trigger.focus(), items: trackItems(kind, track, label)});
  }

  /* 轨道的操作项，抬头的“⋯”与右键共用。 */
  function trackItems(kind, track, label) {
    if (!state.editing) return [];
    const flip = (field, name) => ({label: (track[field] ? '打开' : '关闭') + name,
      icon: icon(field === 'hidden' ? (track.hidden ? 'eye' : 'eye-off') : (track.muted ? 'volume' : 'mute')),
      pick: () => { track[field] = !track[field]; changed(); }});
    return [
      kind !== 'audio' ? flip('hidden', '画面') : null,
      kind !== 'subtitle' ? flip('muted', '声音') : null,
      {label: track.locked ? '解锁这一行' : '锁定这一行', icon: icon(track.locked ? 'unlock' : 'lock'), pick: () => toggleLock(track, label)},
      track.locked ? null : {label: '清空轨道', icon: icon('trash'), pick: () => editLane('已清空 ' + label + '。', () => {
        if (kind === 'subtitle') track.cues.length = 0; else track.clips.length = 0;
        state.timelineSel = null;
      })},
      track.locked || kind === 'video' && track === timeline().video[0] ? null : {label: '移除轨道', icon: icon('alert'), pick: () => editLane('已移除 ' + label + '。', () => {
        const t = timeline();
        (kind === 'subtitle' ? t.subtitles : kind === 'audio' ? t.audio : t.video).splice(
          (kind === 'subtitle' ? t.subtitles : kind === 'audio' ? t.audio : t.video).indexOf(track), 1);
        state.timelineSel = null;
      })}
    ].filter(Boolean);
  }

  /* 左右边缘的裁剪手柄；只读或锁定时不出现。data-handle 说明拖的是哪一边。 */
  const edgeHandles = (cls, locked) => state.editing && !locked ? [
    el('span', {class: cls + ' is-start', 'data-handle': 'start', title: '拖动裁剪开头'}),
    el('span', {class: cls, 'data-handle': 'end', title: '拖动裁剪结尾'})
  ] : [];

  /* 自由轨上的片段：位置与长度由文档里的 in/out 决定，不是镜头格；宽度按比例尺算，排布时才定。 */
  function mediaClipNode(clip, track) {
    return el('div', {class: 'media-clip', 'data-id': clip.id, 'data-track': track.id, role: 'button', tabindex: state.editing ? '0' : '-1',
      title: baseName(clip.source)}, [
      el('span', {class: 'media-clip-name', text: baseName(clip.source)}),
      ...edgeHandles('media-clip-handle', track.locked)
    ]);
  }

  /* 字幕条目：文字就是条目本身；逐字时间只用于高亮，不额外占位。 */
  function cueNode(cue, track) {
    return el('div', {class: 'cue', 'data-id': cue.id, 'data-track': track.id, role: 'button', tabindex: state.editing ? '0' : '-1',
      title: cue.text}, [
      el('span', {class: 'cue-text', text: cue.text}),
      ...edgeHandles('media-clip-handle', track.locked)
    ]);
  }

  /* 按比例尺排布标尺、片段与播放头。拖时长时沿用开始拖动时的比例尺，避免整条时间线跟着缩放。
     左侧抬头占 --gutter 宽度；标尺与泳道在第 2 列，内部坐标从 0 起算，只有跨列的播放头要加这段偏移。 */
  function layoutTimeline(pps) {
    const {placed, total: planned} = plan();
    const total = span();
    const gutter = layout.gutter = parseFloat(getComputedStyle($('timeline')).getPropertyValue('--gutter')) || 0;
    layout.pps = pps > 0 ? pps : 0;
    if (!layout.pps) { placePlayhead(); return; }   // 视图隐藏时先不排，等切回来再按真实宽度算
    $('timeline').style.inlineSize = gutter + total * pps + 'px';
    // 镜头计划之外被素材撑出来的部分：斜纹底 + 分界线，提示这段时间不属于分镜表格
    const beyond = $('beyond'), extra = Math.max(0, total - planned);
    beyond.hidden = extra <= 1e-6;
    if (extra > 1e-6) {
      beyond.style.left = gutter + planned * pps + 'px';
      beyond.style.inlineSize = extra * pps + 'px';
    }
    const [step, parts] = TICK_STEPS.find(([candidate]) => candidate * pps >= TICK_GAP) || TICK_STEPS[TICK_STEPS.length - 1];
    $('ruler').style.setProperty('--minor', step * pps / parts + 'px');
    const marks = total > 0 ? ticks(total, step) : [];
    // 末尾刻度离上一个太近时只画刻度线、不写数字，免得两个标签叠在一起
    if (marks.length > 1 && (marks[marks.length - 1].at - marks[marks.length - 2].at) * pps < TICK_GAP * 0.9) marks[marks.length - 1].label = false;
    $('ruler').replaceChildren(...marks.map(({at, label}, index) => {
      const node = el('div', {class: 'tick' + (index === 0 ? ' first' : '') + (index === marks.length - 1 ? ' last' : '')},
        label ? el('span', {text: round(at) + ' 秒'}) : null);
      node.style.left = at * pps + 'px';
      return node;
    }));
    for (const item of placed) {
      const node = $('track').querySelector('[data-id="' + CSS.escape(item.shot.id) + '"]');
      if (!node) continue;
      node.style.left = item.start * pps + 'px';
      node.style.width = item.length * pps + 'px';
      if (dragging) node.querySelector('.clip-dur').textContent = round(item.length) + '秒';
    }
    layoutLanes(pps);
    placePlayhead();
    updateNav();
  }

  /* 自由轨上的视频/音频片段与字幕条目：位置与长度都来自文档，按比例尺换算成像素。 */
  function layoutLanes(pps) {
    const t = timeline();
    if (!t) return;
    for (const lane of $('lanes').querySelectorAll('.lane[data-kind]')) {
      if (lane.dataset.kind === 'video' && lane.classList.contains('lane-main')) continue;
      const track = (lane.dataset.kind === 'subtitle' ? t.subtitles : lane.dataset.kind === 'audio' ? t.audio : t.video)
        .find(candidate => candidate.id === lane.dataset.id);
      if (!track) continue;
      for (const node of lane.querySelectorAll('.media-clip')) {
        const clip = track.clips.find(candidate => candidate.id === node.dataset.id);
        if (!clip) continue;
        node.style.left = clip.start_us / US * pps + 'px';
        node.style.width = Math.max(2, (clip.out_us - clip.in_us) / US * pps) + 'px';
      }
      for (const node of lane.querySelectorAll('.cue')) {
        const cue = track.cues.find(candidate => candidate.id === node.dataset.id);
        if (!cue) continue;
        node.style.left = cue.start_us / US * pps + 'px';
        node.style.width = Math.max(2, (cue.end_us - cue.start_us) / US * pps) + 'px';
      }
    }
    for (const node of $('marker-band').querySelectorAll('.marker')) {
      const marker = t.markers.find(candidate => candidate.id === node.dataset.marker);
      if (!marker) continue;
      node.style.left = marker.start_us / US * pps + 'px';
      node.style.width = marker.end_us === null ? '' : Math.max(6, (marker.end_us - marker.start_us) / US * pps) + 'px';
    }
  }

  /* ---------- 标记：一个时间点或一段范围上的说明，用户与 Agent 都可以写（author 区分），随文档保存 ---------- */
  const selectedMarker = () => {
    const sel = state.timelineSel, t = timeline();
    return sel && sel.marker && t ? t.markers.find(marker => marker.id === sel.id) || null : null;
  };

  function markerNode(marker) {
    const range = marker.end_us !== null;
    return el('div', {class: 'marker' + (range ? ' is-range' : '') + (marker.author === 'agent' ? ' is-agent' : ''),
      'data-marker': marker.id, role: 'button', tabindex: '0', title: marker.text || (range ? '范围标记' : '标记')}, [
      el('span', {class: 'marker-text', text: marker.text}),
      ...(range ? edgeHandles('marker-handle', markersLocked()) : [])
    ]);
  }

  function renderMarkers() {
    const t = timeline(), locked = markersLocked();
    syncKeyed($('marker-band'), t ? t.markers.map(markerNode) : [], 'data-marker');
    $('marker-band').classList.toggle('is-locked', locked);
    const lock = $('markers-lock');
    lock.setAttribute('aria-pressed', String(locked));
    lock.setAttribute('aria-disabled', state.editing ? 'false' : 'true');
    lock.title = locked ? '解锁标记' : '锁定标记';
    lock.querySelector('use').setAttribute('href', locked ? '#i-lock' : '#i-unlock');
    if (layout.pps > 0) layoutLanes(layout.pps);
    markLaneSelection();
  }

  function toggleMarkersLock() {
    if (!state.editing) return;
    editLane(markersLocked() ? '标记已解锁。' : '标记已锁定：不能新增、移动、改写或删除，联动也不移动它们。', () => {
      ensureTimeline().markers_locked = !markersLocked();
    });
  }

  function selectMarker(id) {
    state.timelineSel = {marker: true, id};
    markLaneSelection();
    syncDetail();
  }

  /* 在播放头加一个点标记；同一时刻已有点标记就直接选中它。 */
  function addMarker() {
    if (!state.editing) return;
    if (markersLocked()) { status(LOCKED); return; }
    const t = ensureTimeline(), at = snapUs(preview.time * US);
    const same = t.markers.find(marker => marker.end_us === null && marker.start_us === at);
    if (same) { selectMarker(same.id); focusDetailText(); return; }
    editLane('已添加标记：在右侧填写说明。', () => {
      const marker = {id: newTimelineId('m'), start_us: at, end_us: null, text: '', author: 'user'};
      t.markers.push(marker);
      t.markers.sort((a, b) => a.start_us - b.start_us);
      state.timelineSel = {marker: true, id: marker.id};
    });
    focusDetailText();
  }

  /* 右键菜单：只列当前位置可用的操作；“在此处……”先把播放头移到右键的位置。只读时只有查看类操作。 */
  function setupContextMenu() {
    $('timeline-scroll').addEventListener('contextmenu', event => {
      const items = contextItems(event).filter(Boolean);
      event.preventDefault();
      if (!items.length) return;
      const point = new DOMRect(event.clientX, event.clientY, 0, 0);
      openMenu({anchor: {getBoundingClientRect: () => point}, align: 'start', label: '时间线操作', restore: () => {}, items});
    });
  }

  function contextItems(event) {
    const target = event.target, editing = state.editing, here = () => seekAt(event.clientX);
    const markerItems = () => editing && !markersLocked() ? [{label: '在此处添加标记', icon: icon('marker'), hint: 'M', pick: () => { here(); addMarker(); }}] : [];
    const node = target.closest('.marker');
    if (node) {
      selectMarker(node.dataset.marker);
      return [
        {label: '编辑说明', icon: icon('text'), pick: focusDetailText},
        editing && !markersLocked() ? {label: '删除标记', icon: icon('trash'), hint: 'Delete', pick: deleteItem} : null,
        editing ? {label: markersLocked() ? '解锁标记' : '锁定标记', icon: icon(markersLocked() ? 'unlock' : 'lock'), pick: toggleMarkersLock} : null
      ];
    }
    if (target.closest('#marker-band, #ruler')) return markerItems();
    const item = target.closest('.media-clip, .cue');
    if (item) {
      laneSelect(item);
      const found = selectedItem(), label = laneLabel(found.kind, found.track), free = editing && !found.track.locked;
      return [
        found.kind === 'subtitle' ? {label: '编辑文字', icon: icon('text'), pick: focusDetailText} : null,
        free ? {label: '在此处分割', icon: icon('cut'), hint: 'Ctrl+K', pick: () => { here(); splitItem(); }} : null,
        free ? {label: '删除', icon: icon('trash'), hint: 'Delete', pick: deleteItem} : null,
        editing ? {label: found.track.locked ? '解锁这一行' : '锁定这一行', icon: icon(found.track.locked ? 'unlock' : 'lock'),
          pick: () => toggleLock(found.track, label)} : null
      ];
    }
    const shotNode = target.closest('#track .clip');
    if (shotNode) {
      const shot = shotById(shotNode.dataset.id), clip = shot && mainClip(shot), free = editing && !mainLocked();
      select(shot.id);
      return [
        ...(free ? videoItems(shot) : []),
        editing && timeline() ? {label: mainLocked() ? '解锁这一行' : '锁定这一行', icon: icon(mainLocked() ? 'unlock' : 'lock'),
          pick: () => toggleLock(timeline().video[0], 'V1')} : null,
        ...markerItems()
      ];
    }
    const lane = target.closest('.lane[data-kind]');
    if (!lane) return [];
    const t = timeline(), kind = lane.dataset.kind;
    const track = t && (kind === 'subtitle' ? t.subtitles : kind === 'audio' ? t.audio : t.video).find(item => item.id === lane.dataset.id);
    if (!track) return markerItems();
    if (target.closest('.lane-head')) return trackItems(kind, track, laneLabel(kind, track));
    const open = editing && !track.locked, main = track === t.video[0];
    return [
      open && kind === 'subtitle' ? {label: '在此处新建字幕', icon: icon('text'), pick: () => { here(); addCue(track); }} : null,
      open && kind === 'video' && !main ? {label: '在此处插入视频…', icon: icon('film'), pick: () => { here(); insertMedia('video', track); }} : null,
      open && kind === 'audio' ? {label: '在此处插入音频…', icon: icon('volume'), pick: () => { here(); insertMedia('audio', track); }} : null,
      ...markerItems(),
      ...trackItems(kind, track, laneLabel(kind, track))
    ];
  }

  /* 轨道的显示名：只用编号 S1、V2、A1，与抬头一致。 */
  function laneLabel(kind, track) {
    const t = timeline();
    const code = kind === 'subtitle' ? 'S' + (t.subtitles.indexOf(track) + 1)
      : kind === 'audio' ? 'A' + (t.audio.indexOf(track) + 1) : 'V' + (t.video.indexOf(track) + 1);
    return code;
  }

  function setupEditToggles() {
    const sync = () => {
      $('snap-toggle').setAttribute('aria-pressed', String(snapOn()));
      $('link-toggle').setAttribute('aria-pressed', String(linkOn()));
    };
    $('snap-toggle').addEventListener('click', toggleSnap);
    $('link-toggle').addEventListener('click', () => {
      prefs.link = !linkOn();
      sync(); savePrefs();
      status(linkOn() ? '联动已打开：镜头时长变化时，其他轨道的条目跟着平移。' : '联动已关闭：改镜头时长不再移动其他轨道。');
    });
    sync();
  }

  function toggleSnap() {
    prefs.snap = !snapOn();
    $('snap-toggle').setAttribute('aria-pressed', String(snapOn()));
    savePrefs();
    status(snapOn() ? '吸附已打开。' : '吸附已关闭：只按 0.1 秒取整。');
  }

  /* 标记带：点选标记；解锁后拖动标记移动、拖范围标记两侧调整起止，在空白处拖动画出新的范围标记。 */
  function setupMarkers() {
    const band = $('marker-band');
    let drag = null;
    const timeAt = clientX => (clientX - band.getBoundingClientRect().left) / layout.pps * US;
    band.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !layout.pps) return;
      const node = event.target.closest('.marker');
      if (node) selectMarker(node.dataset.marker);
      else if (state.timelineSel && state.timelineSel.marker) { state.timelineSel = null; markLaneSelection(); syncDetail(); }
      if (!state.editing || markersLocked()) return;
      event.preventDefault();
      const marker = node ? timeline().markers.find(item => item.id === node.dataset.marker) : null;
      drag = {marker, x: event.clientX, from: timeAt(event.clientX), before: JSON.stringify(state.working), moved: false, created: false,
        edge: node && event.target.dataset.handle || null, start: marker && marker.start_us, end: marker && marker.end_us,
        targets: snapTargets(new Set(marker ? [marker] : []))};
      band.setPointerCapture(event.pointerId);
      if (node) node.focus();
    });
    band.addEventListener('pointermove', event => {
      if (!drag) return;
      if (!drag.moved && Math.abs(event.clientX - drag.x) < 3) return;
      drag.moved = true;
      const raw = (event.clientX - drag.x) / layout.pps * US, MIN = 100000;
      if (!drag.marker) {
        const from = settle(drag.from, [0], drag.targets, layout.pps);
        drag.marker = {id: newTimelineId('m'), start_us: from, end_us: from + MIN, text: '', author: 'user'};
        drag.created = true;
        drag.from = from;
        ensureTimeline().markers.push(drag.marker);
        state.timelineSel = {marker: true, id: drag.marker.id};
        renderMarkers();
      }
      const marker = drag.marker;
      if (drag.created) {
        const at = settle(timeAt(event.clientX), [0], drag.targets, layout.pps);
        marker.start_us = Math.min(drag.from, at);
        marker.end_us = Math.max(marker.start_us + MIN, Math.max(drag.from, at));
      } else if (drag.edge === 'end') {
        marker.end_us = Math.max(drag.start + MIN, settle(drag.end + raw, [0], drag.targets, layout.pps));
      } else if (drag.edge === 'start') {
        marker.start_us = Math.min(drag.end - MIN, settle(drag.start + raw, [0], drag.targets, layout.pps));
      } else {
        const range = drag.end === null ? 0 : drag.end - drag.start;
        marker.start_us = settle(drag.start + raw, range ? [0, range] : [0], drag.targets, layout.pps);
        if (drag.end !== null) marker.end_us = marker.start_us + range;
      }
      layoutTimeline(layout.pps);
    });
    const finish = () => {
      if (!drag) return;
      const {moved, before, created} = drag;
      drag = null;
      showSnap(null);
      if (!moved) return;
      timeline().markers.sort((a, b) => a.start_us - b.start_us);
      remember(before);
      changed();
      syncDetail(true);
      status(created ? '已添加范围标记：在右侧填写说明。' : '已调整标记。');
      if (created) focusDetailText();
    };
    band.addEventListener('pointerup', finish);
    band.addEventListener('pointercancel', finish);
    $('marker-add').addEventListener('click', addMarker);
    $('markers-lock').addEventListener('click', toggleMarkersLock);
  }

  /* ---------- 自由轨编辑：选中、移动、右缘裁剪、分割、删除与撤销 ----------
     每次改动前把整份文档拍一张快照压栈；撤销与重做就是换回快照，栈只留在内存里、不写进文档。
     吸附与主轨一致，按 0.1 秒取整。 */
  const UNDO_LIMIT = 50;

  /* 锁定：写在文档里（轨道 locked、时间线 markers_locked）。锁定的行不能拖动、裁剪、分割、删除或新增，联动也不移动它；
     选中、开关画面与声音照常。 */
  const markersLocked = () => Boolean(timeline() && timeline().markers_locked);
  const mainLocked = () => Boolean(timeline() && timeline().video[0].locked);
  const LOCKED = '这一行已锁定，先在左侧解锁。';

  /* 吸附：拖动时某条边离任何条目边缘、镜头切点、标记或播放头不到 8 像素就贴上去，并显示吸附线；
     关掉吸附时只按 0.1 秒取整。开关是界面偏好（snap），默认打开。 */
  const SNAP_PX = 8;
  const snapOn = () => prefs.snap !== false;
  const linkOn = () => prefs.link !== false;

  function snapTargets(skip) {
    const t = timeline(), out = [0, Math.round(preview.time * US)];
    for (const {start, length} of plan().placed) out.push(Math.round(start * US), Math.round((start + length) * US));
    if (!t) return out;
    for (const track of [...t.video.slice(1), ...t.audio]) {
      for (const clip of track.clips) if (!skip.has(clip)) out.push(clip.start_us, clip.start_us + clip.out_us - clip.in_us);
    }
    for (const track of t.subtitles) for (const cue of track.cues) if (!skip.has(cue)) out.push(cue.start_us, cue.end_us);
    for (const marker of t.markers) if (!skip.has(marker)) out.push(marker.start_us, ...(marker.end_us === null ? [] : [marker.end_us]));
    return out;
  }

  function showSnap(at) {
    const line = $('snap-line');
    line.hidden = at === null;
    if (at !== null) line.style.left = trackX(at / US) + 'px';
  }

  /* 求锚点的位置（微秒）。offsets 是锚点到各条会贴靠的边的距离；有边靠近目标就整体贴过去，否则按 0.1 秒取整。 */
  function settle(raw, offsets, targets, pps) {
    if (snapOn() && targets) {
      const limit = SNAP_PX / pps * US;
      let best = null;
      for (const offset of offsets) {
        for (const target of targets) {
          const shift = target - (raw + offset);
          if (Math.abs(shift) <= limit && (!best || Math.abs(shift) < Math.abs(best.shift))) best = {shift, at: target};
        }
      }
      if (best) { showSnap(best.at); return Math.max(0, Math.round(raw + best.shift)); }
    }
    showSnap(null);
    return snapUs(raw);
  }

  /* 联动：镜头时长变了，让其他轨道的条目跟着它开始时所在的镜头走（镜头计划之后的跟着总长走）。
     base 是改动前拍下的镜头开始时间与条目位置；锁定的行不参与。平移后若同一轨道挤在一起，按顺序往后推开。 */
  function linkSnapshot() {
    const t = timeline();
    if (!linkOn() || !t) return null;
    const {placed, total} = plan(), items = [];
    for (const track of [...t.video.slice(1), ...t.audio]) {
      if (!track.locked) for (const clip of track.clips) items.push({item: clip, kind: 'clip', track, start: clip.start_us});
    }
    for (const track of t.subtitles) if (!track.locked) for (const cue of track.cues) items.push({item: cue, kind: 'subtitle', track, start: cue.start_us});
    if (!t.markers_locked) for (const marker of t.markers) items.push({item: marker, kind: 'marker', track: null, start: marker.start_us});
    return {cuts: placed.map(entry => [entry.shot.id, Math.round(entry.start * US)]), total: Math.round(total * US), items};
  }

  /* stay：只把条目放回拍快照时的位置（移动切点不改总长，其他轨道不动）。 */
  function applyLink(base, stay) {
    if (!base) return;
    const {placed, total} = plan(), now = new Map(placed.map(entry => [entry.shot.id, Math.round(entry.start * US)]));
    const shift = Math.round(total * US) - base.total;
    const map = time => {
      if (stay) return time;
      if (time >= base.total) return time + shift;
      let owner = null;
      for (const cut of base.cuts) { if (cut[1] <= time) owner = cut; else break; }
      return owner && now.has(owner[0]) ? time - owner[1] + now.get(owner[0]) : time;
    };
    const moveTo = (entry, start) => {
      const item = entry.item;
      if (entry.kind === 'subtitle') moveCue(item, start, start + item.end_us - item.start_us);
      else if (entry.kind === 'marker') { if (item.end_us !== null) item.end_us += start - item.start_us; item.start_us = start; }
      else item.start_us = start;
    };
    for (const entry of base.items) moveTo(entry, Math.max(0, map(entry.start)));
    const tracks = new Set(base.items.map(entry => entry.track).filter(Boolean));
    for (const track of tracks) {
      const kind = track.cues ? 'subtitle' : 'clip';
      let end = 0;
      for (const item of (track.cues || track.clips).slice().sort((a, b) => a.start_us - b.start_us)) {
        const span = spanOf(item, kind);
        if (span.start < end) moveTo({item, kind}, end);
        end = Math.max(end, spanOf(item, kind).end);
      }
    }
  }

  const remember = before => {
    history.past.push(before);
    if (history.past.length > UNDO_LIMIT) history.past.shift();
    history.future.length = 0;
  };
  const history = {past: [], future: []};
  // 绝对时间不能为负；拖动量是有符号增量，负数必须保留，否则条目只能往右走
  const snapUs = value => Math.max(0, Math.round(value / 100000) * 100000);
  const snapDelta = value => Math.round(value / 100000) * 100000;

  function findTrack(trackId) {
    const t = timeline();
    if (!t) return null;
    for (const [kind, list] of [['subtitle', t.subtitles], ['video', t.video], ['audio', t.audio]]) {
      const track = list.find(item => item.id === trackId);
      if (track) return {track, kind, items: (kind === 'subtitle' ? track.cues : track.clips).slice()};
    }
    return null;
  }

  const spanOf = (item, kind) => kind === 'subtitle'
    ? {start: item.start_us, end: item.end_us}
    : {start: item.start_us, end: item.start_us + item.out_us - item.in_us};

  /* 字段时间与所属条目绑定：移动时整体平移，裁剪时把越界的时间收进条目，完全超出的丢弃。
     校验要求每段时间按顺序、落在条目之内、互不重叠，所以这里不能只改条目本身。 */
  function moveCue(cue, start, end) {
    const delta = start - cue.start_us;
    cue.start_us = start;
    cue.end_us = end;
    cue.words = cue.words.map(word => ({...word, start_us: word.start_us + delta, end_us: word.end_us + delta}))
      .filter(word => word.start_us >= start && word.end_us <= end);
  }

  function trimCue(cue, end) {
    cue.end_us = end;
    cue.words = cue.words.filter(word => word.end_us <= end)
      .map(word => word);
  }

  /* 同一轨道里上一个的结尾与下一个的开头：移动与裁剪都不允许越过它们。items 是 findTrack 取出的条目副本列表。 */
  function neighbours(items, kind, item) {
    const spans = items.filter(other => other !== item).map(other => ({item: other, ...spanOf(other, kind)}))
      .sort((a, b) => a.start - b.start);
    const at = spanOf(item, kind);
    const before = spans.filter(span => span.end <= at.start).pop();
    const after = spans.find(span => span.start >= at.end);
    return {min: before ? before.end : 0, max: after ? after.start : Infinity};
  }

  function selectedItem() {
    const sel = state.timelineSel;
    if (!sel) return null;
    const found = findTrack(sel.track);
    const item = found && found.items.find(entry => entry.id === sel.id);
    return item ? {...found, item} : null;
  }

  function markLaneSelection() {
    const sel = state.timelineSel;
    markSelection();
    for (const node of $('lanes').querySelectorAll('.media-clip, .cue')) {
      const selected = Boolean(sel && !sel.marker && node.dataset.id === sel.id);
      node.classList.toggle('is-selected', selected);
      node.setAttribute('aria-pressed', String(selected));
    }
    for (const node of $('marker-band').querySelectorAll('.marker')) {
      const selected = Boolean(sel && sel.marker && node.dataset.marker === sel.id);
      node.classList.toggle('is-selected', selected);
      node.setAttribute('aria-pressed', String(selected));
    }
  }

  function laneSelect(node) {
    state.timelineSel = node ? {track: node.dataset.track, id: node.dataset.id} : null;
    markLaneSelection();
    syncDetail();
  }

  /* 拍快照后执行修改，再统一刷新；reason 只用于状态栏提示。 */
  function editLane(reason, mutate) {
    history.past.push(JSON.stringify(state.working));
    if (history.past.length > UNDO_LIMIT) history.past.shift();
    history.future.length = 0;
    mutate();
    changed();
    markLaneSelection();
    syncDetail(true);
    status(reason);
  }

  function timeTravel(back) {
    const from = back ? history.past : history.future, to = back ? history.future : history.past;
    if (!from.length) { status(back ? '没有可撤销的修改。' : '没有可重做的修改。'); return; }
    to.push(JSON.stringify(state.working));
    state.working = JSON.parse(from.pop());
    state.dirty = true;
    if (!shotById(state.selectedId)) state.selectedId = shots().length ? shots()[0].id : null;
    render();
    status(back ? '已撤销。' : '已重做。');
  }

  /* 分割：播放头落在条目内部时切成两段；字幕条目按文字所在位置切开逐字时间。 */
  function splitItem() {
    const found = selectedItem(), t = timeline();
    if (!found || !t) return;
    if (found.track.locked) { status(LOCKED); return; }
    const at = snapUs(preview.time * US), span = spanOf(found.item, found.kind);
    if (at <= span.start || at >= span.end) { status('把播放头放到条目中间再分割。'); return; }
    editLane('已在播放头处分割。', () => {
      const {item, track, kind} = found;
      const tail = {id: newTimelineId('n'), start_us: at, end_us: span.end};
      if (kind === 'subtitle') {
        // 逐段时间按播放头分到前后两段：整段在切点之前的留在前半，其余归后半并把起点提到切点
        tail.text = item.text;
        tail.words = item.words.filter(word => word.end_us > at)
          .map(word => ({...word, start_us: Math.max(word.start_us, at)}));
        item.words = item.words.filter(word => word.end_us <= at);
      } else {
        tail.in_us = item.in_us + (at - item.start_us);
        tail.out_us = item.out_us;
        tail.source = item.source;
        if ('audio' in item) tail.audio = item.audio;
        item.out_us = tail.in_us;
      }
      item.end_us = at;
      track.cues ? track.cues.push(tail) : track.clips.push(tail);
      track.cues ? track.cues.sort((a, b) => a.start_us - b.start_us) : track.clips.sort((a, b) => a.start_us - b.start_us);
      state.timelineSel = {track: track.id, id: tail.id};
    });
  }

  /* 方向键：按住 Shift 走 1 秒，否则 0.1 秒，与主轨的时长微调同一手感。 */
  function nudgeLane(direction, coarse) {
    const found = selectedItem();
    if (!found) return;
    if (found.track.locked) { status(LOCKED); return; }
    const step = (coarse ? 1 : 0.1) * US;
    const span = spanOf(found.item, found.kind), bounds = neighbours(found.items, found.kind, found.item);
    const length = span.end - span.start;
    const start = Math.min(bounds.max - length, Math.max(bounds.min, span.start + direction * step));
    if (start === span.start) return;
    editLane('已移到 ' + round(start / US) + ' 秒。', () => {
      if (found.kind === 'subtitle') moveCue(found.item, start, start + length);
      else found.item.start_us = start;
    });
  }

  function deleteItem() {
    const marker = selectedMarker();
    if (marker) {
      if (markersLocked()) { status(LOCKED); return; }
      editLane('已删除标记。', () => {
        timeline().markers.splice(timeline().markers.indexOf(marker), 1);
        state.timelineSel = null;
      });
      return;
    }
    const found = selectedItem();
    if (!found) return;
    if (found.track.locked) { status(LOCKED); return; }
    editLane('已删除。', () => {
      const list = found.kind === 'subtitle' ? found.track.cues : found.track.clips;
      list.splice(list.indexOf(found.item), 1);
      state.timelineSel = null;
    });
  }

  /* 拖动：按在右缘手柄上裁尾，按在条目本身上整体移动。 */
  /* 轨道抬头按钮的点击：按所在轨道找到对象再处理，节点复用后也不会拿到旧对象。 */
  function setupLaneHeads() {
    $('lanes').addEventListener('click', event => {
      const button = event.target.closest('.lane-head button[data-act]');
      const lane = button && button.closest('.lane'), t = timeline();
      if (!lane || !t) return;
      const kind = lane.dataset.kind;
      const track = (kind === 'subtitle' ? t.subtitles : kind === 'audio' ? t.audio : t.video).find(item => item.id === lane.dataset.id);
      if (!track || !state.editing) return;
      const act = button.dataset.act, label = laneLabel(kind, track);
      if (act === 'more') openTrackActions(button, kind, track, label);
      else if (act === 'locked') toggleLock(track, label);
      else { track[act] = !track[act]; changed(); }
    });
  }

  function setupLaneDrag() {
    setupLaneHeads();
    const lanes = $('lanes');
    let drag = null;
    lanes.addEventListener('pointerdown', event => {
      const node = event.target.closest('.media-clip, .cue');
      if (!state.editing || !node || event.button !== 0) return;
      const found = findTrack(node.dataset.track);
      const item = found && found.items.find(entry => entry.id === node.dataset.id);
      if (!item) return;
      laneSelect(node);
      if (found.track.locked) return;
      const span = spanOf(item, found.kind);
      drag = {node, item, kind: found.kind, track: found.track, x: event.clientX, pps: layout.pps,
        edge: event.target.dataset.handle || null, span, bounds: neighbours(found.items, found.kind, item),
        inUs: item.in_us, words: item.words ? item.words.slice() : null,
        targets: snapTargets(new Set([item])), moved: false, before: JSON.stringify(state.working)};
      node.setPointerCapture(event.pointerId);
      node.focus();
      event.preventDefault();
    });
    lanes.addEventListener('pointermove', event => {
      if (!drag) return;
      const raw = (event.clientX - drag.x) / drag.pps * US;
      if (!drag.moved && Math.abs(event.clientX - drag.x) < 2) return;
      drag.moved = true;
      $('timeline').classList.add('is-dragging');
      drag.node.classList.add('is-dragging');
      const {span, bounds, item, kind} = drag, length = span.end - span.start, MIN = 100000;
      if (drag.edge === 'end') {
        const end = Math.min(bounds.max, Math.max(span.start + MIN, settle(span.end + raw, [0], drag.targets, drag.pps)));
        if (kind === 'subtitle') { item.end_us = end; item.words = drag.words.filter(word => word.end_us <= end); }
        else item.out_us = item.in_us + (end - item.start_us);
      } else if (drag.edge === 'start') {
        // 片段的开头不能早于素材开头（入点不小于 0）
        const floor = kind === 'subtitle' ? bounds.min : Math.max(bounds.min, span.start - drag.inUs);
        const start = Math.max(floor, Math.min(span.end - MIN, settle(span.start + raw, [0], drag.targets, drag.pps)));
        if (kind === 'subtitle') { item.start_us = start; item.words = drag.words.filter(word => word.start_us >= start); }
        else { item.in_us = drag.inUs + (start - span.start); item.start_us = start; }
      } else {
        const start = Math.min(bounds.max - length, Math.max(bounds.min, settle(span.start + raw, [0, length], drag.targets, drag.pps)));
        if (kind === 'subtitle') moveCue(item, start, start + length);
        else item.start_us = start;
      }
      // 拖到原有时长之外时，时间线要跟着变长，否则条目会跑到滚动区外面、再也点不到
      layoutTimeline(drag.pps);
    });
    const finish = commit => {
      if (!drag) return;
      const {moved, node, before, item, edge, kind} = drag;
      drag = null;
      showSnap(null);
      $('timeline').classList.remove('is-dragging');
      node.classList.remove('is-dragging');
      if (!moved) return;
      if (!commit) { state.working = JSON.parse(before); changed(); return; }
      // 拖动过程中已经把位置写进文档，撤销栈记拖动前的快照
      remember(before);
      changed();
      syncDetail(true);
      const now = spanOf(item, kind);
      status(edge
        ? '已调整长度到 ' + round((now.end - now.start) / US) + ' 秒。'
        : '已移动到 ' + round(now.start / US) + ' 秒。');
    };
    // 在窗口或泳道外松开鼠标也要收尾，否则拖动状态会挂住、之后的光标移动还会继续挪条目
    window.addEventListener('pointerup', () => finish(true));
    window.addEventListener('pointercancel', () => finish(false));
    window.addEventListener('blur', () => finish(false));
    $('view-timeline').addEventListener('keydown', event => {
      if (event.key === 'Escape' && drag) { finish(false); }
    });
  }

  /* 以屏幕横坐标 clientX 处的时间点为锚缩放：缩放后该时间点仍停在原来的位置。 */
  function setZoom(pps, clientX) {
    const fit = fitPps();
    if (!fit || !layout.pps) return;
    const scroller = $('timeline-scroll'), timeline = $('timeline');
    const time = (clientX - timeline.getBoundingClientRect().left - layout.gutter) / layout.pps;
    const next = Math.min(Math.max(pps, fit), Math.max(MAX_PPS, fit));
    zoom.pps = next <= fit * 1.001 ? null : next;
    layoutTimeline(currentPps());
    scroller.scrollLeft += timeline.getBoundingClientRect().left + layout.gutter + time * layout.pps - clientX;
  }

  /* 按钮与快捷键缩放以播放头为锚；播放头不在可见区时以可见区中央为锚。 */
  function zoomAnchor() {
    const rect = $('timeline-scroll').getBoundingClientRect();
    const x = $('timeline').getBoundingClientRect().left + trackX(Math.min(preview.time, span()));
    return x >= rect.left && x <= rect.right ? x : rect.left + rect.width / 2;
  }
  const zoomBy = factor => setZoom(currentPps() * factor, zoomAnchor());
  const zoomFit = () => setZoom(0, zoomAnchor());

  /* 导航条代替原生滚动条：滑块的位置与长度对应可见区在整条时间线中的位置与比例；底部横向、右侧纵向。 */
  function updateNav() {
    const scroller = $('timeline-scroll'), width = scroller.scrollWidth || 1, height = scroller.scrollHeight || 1;
    const thumb = $('timeline-nav-thumb'), vthumb = $('timeline-vnav-thumb');
    thumb.style.left = scroller.scrollLeft / width * 100 + '%';
    thumb.style.width = Math.min(1, scroller.clientWidth / width) * 100 + '%';
    // 轨道放得下时没有可滚的余量，竖向导航条整列收起
    $('timeline-frame').classList.toggle('is-flat', height <= scroller.clientHeight + 1);
    vthumb.style.top = scroller.scrollTop / height * 100 + '%';
    vthumb.style.height = Math.min(1, scroller.clientHeight / height) * 100 + '%';
  }

  /* 拖动滑块平移；点击空白处先把可见区中心移到点击位置，再接着拖。 */
  function setupNav(nav, horizontal) {
    const scroller = $('timeline-scroll');
    const key = horizontal ? 'scrollLeft' : 'scrollTop', axis = horizontal ? 'clientX' : 'clientY';
    nav.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      const rect = nav.getBoundingClientRect();
      const ratio = horizontal ? scroller.scrollWidth / rect.width : scroller.scrollHeight / rect.height;
      if (event.target === nav) {
        scroller[key] = (event[axis] - (horizontal ? rect.left : rect.top)) * ratio
          - (horizontal ? scroller.clientWidth : scroller.clientHeight) / 2;
      }
      const start = event[axis], from = scroller[key];
      nav.classList.add('is-dragging');
      followPointer(nav, event, move => { scroller[key] = from + (move[axis] - start) * ratio; },
        () => nav.classList.remove('is-dragging'));
    });
  }

  /* 按住拖动：指针捕获到 node 上，松开或取消时结束。 */
  function followPointer(node, event, onMove, onEnd) {
    node.setPointerCapture(event.pointerId);
    // 拖动期间整页禁止选中文字，并清掉已有选区；类在 pointerdown 内同步加上，早于浏览器开始选区
    document.documentElement.classList.add('is-scrubbing');
    getSelection().removeAllRanges();
    const stop = () => {
      document.documentElement.classList.remove('is-scrubbing');
      node.removeEventListener('pointermove', onMove);
      node.removeEventListener('pointerup', stop);
      node.removeEventListener('pointercancel', stop);
      if (onEnd) onEnd();
    };
    node.addEventListener('pointermove', onMove);
    node.addEventListener('pointerup', stop);
    node.addEventListener('pointercancel', stop);
  }

  /* 分隔条：分配监视器与时间线面板的高度、详情栏的宽度；松手后写入偏好，下次打开沿用。 */
  const PANEL_MIN = 200, MONITOR_MIN = 160, ASIDE_MIN = 280, VIEW_MIN = 480;
  function applyLayout() {
    if (prefs.panelH) $('view-timeline').style.setProperty('--panel-h', prefs.panelH + 'px');
    if (prefs.asideW) document.querySelector('.workspace').style.setProperty('--aside-w', prefs.asideW + 'px');
    if (prefs.gutterW) $('timeline-frame').style.setProperty('--gutter', prefs.gutterW + 'px');
  }
  function setPanelHeight(height) {
    prefs.panelH = Math.round(Math.max(PANEL_MIN, Math.min(height, $('view-timeline').clientHeight - MONITOR_MIN)));
    applyLayout();
  }
  const GUTTER_MIN = 100, GUTTER_MAX = 320;
  function setGutterWidth(width) {
    prefs.gutterW = Math.round(Math.max(GUTTER_MIN, Math.min(width, GUTTER_MAX)));
    applyLayout();
    layoutTimeline(currentPps());
  }
  function setAsideWidth(width) {
    prefs.asideW = Math.round(Math.max(ASIDE_MIN, Math.min(width, document.querySelector('.workspace').clientWidth - VIEW_MIN)));
    applyLayout();
  }

  /* 分隔条按住拖动或用方向键调整；指针朝 axis 正方向移动时尺寸减小（面板在下方、详情栏在右侧）。 */
  /* dir：指针沿坐标轴正向移动时尺寸的变化方向；监视器与详情栏的分隔条在被调整区域的起始边，取 -1。 */
  function setupSplitter(splitter, current, resize, axis, keys, dir = -1) {
    splitter.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const start = event[axis], from = current();
      splitter.classList.add('is-dragging');
      followPointer(splitter, event, move => resize(from + dir * (move[axis] - start)),
        () => { splitter.classList.remove('is-dragging'); savePrefs(); });
    });
    splitter.addEventListener('keydown', event => {
      const step = keys[event.key];
      if (!step) return;
      event.preventDefault();
      resize(current() + step);
      savePrefs();
    });
  }

  function setupTimelineView() {
    const scroller = $('timeline-scroll'), nav = $('timeline-nav'), splitter = $('splitter');
    const panel = document.querySelector('.timeline-panel');
    // 滚轮横向滚动，Shift+滚轮纵向滚动；Ctrl 或 Alt+滚轮（含触控板捏合）以指针位置为锚缩放。
    // Windows 上 Chromium 会把 Shift+滚轮的量放进 deltaX，所以两个分量都要看
    scroller.addEventListener('wheel', event => {
      event.preventDefault();
      if (event.ctrlKey || event.altKey) setZoom(currentPps() * Math.exp(-event.deltaY * 0.002), event.clientX);
      else if (event.shiftKey) scroller.scrollTop += event.deltaY || event.deltaX;
      else scroller.scrollLeft += event.deltaX + event.deltaY;
    }, {passive: false});
    scroller.addEventListener('scroll', updateNav);
    new ResizeObserver(() => { if (!dragging) layoutTimeline(currentPps()); }).observe(scroller);
    $('zoom-in').addEventListener('click', () => zoomBy(1.5));
    $('zoom-out').addEventListener('click', () => zoomBy(1 / 1.5));
    $('zoom-fit').addEventListener('click', zoomFit);
    $('track-add').addEventListener('click', event => openTrackMenu(event.currentTarget));

    setupNav(nav, true);
    setupNav($('timeline-vnav'), false);

    applyLayout();
    setupSplitter(splitter, () => panel.offsetHeight, setPanelHeight, 'clientY', {ArrowUp: 16, ArrowDown: -16});
    setupSplitter($('gutter-splitter'), () => layout.gutter, setGutterWidth, 'clientX', {ArrowLeft: -16, ArrowRight: 16}, 1);
    setupSplitter($('aside-splitter'), () => $('detail').offsetWidth, setAsideWidth, 'clientX', {ArrowLeft: 16, ArrowRight: -16});
  }

  /* 按当前顺序重编镜号，沿用现有镜号的前缀与位数（如 S01、S001）；没有可参照的格式时用 S01。 */
  function renumber() {
    const sample = shots().map(shot => /^(\D*)(\d+)$/.exec(shot.number.trim())).find(Boolean);
    const prefix = sample ? sample[1] : 'S';
    const width = Math.max(sample ? sample[2].length : 2, String(shots().length).length);
    shots().forEach((shot, index) => { shot.number = prefix + String(index + 1).padStart(width, '0'); });
  }

  /* 把镜头移到 beforeId 之前；beforeId 为 null 时放到最后一个已排布镜头之后。返回顺序是否变化。 */
  function moveShot(id, beforeId) {
    const list = shots(), order = list.map(shot => shot.id).join('\n');
    const [shot] = list.splice(list.findIndex(item => item.id === id), 1);
    const at = beforeId ? list.findIndex(item => item.id === beforeId)
      : list.reduce((last, item, index) => seconds(item) !== null ? index + 1 : last, 0);
    list.splice(at, 0, shot);
    return list.map(item => item.id).join('\n') !== order;
  }

  /* 时间线上的修改写回文档后，表格、详情与时间线一起刷新，焦点留在被操作的片段上。 */
  function commitTimeline(id, reordered) {
    if (reordered) renumber();
    changed();
    renderTable();
    select(id);
    const shot = shotById(state.selectedId);
    if (shot && $('detail-title')) $('detail-title').textContent = shot.number;
    const node = $('view-timeline').querySelector('.clip[data-id="' + CSS.escape(id) + '"]');
    if (node) node.focus();
  }

  let dragging = null;

  function dropTarget(event) {
    const track = $('track').getBoundingClientRect(), base = $('timeline').getBoundingClientRect();
    if (event.clientY < track.top - 48 || event.clientY > track.bottom + 48) return null;
    const others = [...$('track').children].filter(node => node !== dragging.node);
    const before = others.find(node => { const rect = node.getBoundingClientRect(); return event.clientX < rect.left + rect.width / 2; });
    const edge = before ? before.getBoundingClientRect().left
      : others.length ? others[others.length - 1].getBoundingClientRect().right : track.left;
    return {beforeId: before ? before.dataset.id : null, x: edge - base.left};
  }

  function setupTimelineDrag() {
    const view = $('view-timeline');
    view.addEventListener('pointerdown', event => {
      const node = event.target.closest('.clip');
      if (!state.editing || !node || event.button !== 0 || mainLocked()) return;
      const shot = shotById(node.dataset.id), {placed} = plan(), index = placed.findIndex(item => item.shot === shot);
      const resize = event.target.dataset.edge || null;
      const before = placed[index - 1] || null, cutAt = index >= 0 ? Math.round(placed[index].start * US) : 0;
      // 吸附只贴不会跟着动的东西：本镜之后的切点（以及联动时其后的条目）都会移动，不作为目标
      const fixedEnd = resize === 'start' ? cutAt : cutAt + Math.round(seconds(shot) * US);
      dragging = {node, shot, x: event.clientX, y: event.clientY, moved: false, target: null,
        resize, pending: node.parentElement.id === 'pending-list', prev: before && before.shot,
        prevLength: before ? before.length : 0, cutAt, length: seconds(shot), original: shot.duration_seconds, pps: layout.pps,
        undo: JSON.stringify(state.working), link: resize ? linkSnapshot() : null,
        targets: resize ? snapTargets(new Set()).filter(time => time < fixedEnd || !linkOn() && time > fixedEnd) : null};
      node.setPointerCapture(event.pointerId);
      if (dragging.resize) event.preventDefault();
    });
    view.addEventListener('pointermove', event => {
      if (!dragging) return;
      const dx = event.clientX - dragging.x, dy = event.clientY - dragging.y;
      if (!dragging.moved && !dragging.resize && Math.hypot(dx, dy) < 4) return;
      dragging.moved = true;
      $('timeline').classList.add('is-dragging');
      if (dragging.resize) {
        resizeShot(dragging, dx, event.altKey);
        layoutTimeline(dragging.pps);
        return;
      }
      dragging.node.classList.add('is-dragging');
      dragging.node.style.translate = dx + 'px ' + (dragging.pending ? dy : 0) + 'px';
      dragging.target = dropTarget(event);
      $('drop-mark').hidden = !dragging.target;
      if (dragging.target) $('drop-mark').style.left = dragging.target.x + 'px';
    });
    const finish = commit => {
      if (!dragging) return;
      const {node, shot, moved, resize, pending, target, undo} = dragging;
      dragging = null;
      showSnap(null);
      $('drop-mark').hidden = true;
      $('timeline').classList.remove('is-dragging');
      node.classList.remove('is-dragging');
      node.style.translate = '';
      if (!moved) return;
      suppressClick = true;
      setTimeout(() => { suppressClick = false; });
      if (resize) {
        if (commit && JSON.stringify(state.working) !== undo) { remember(undo); commitTimeline(shot.id, false); return; }
        state.working = JSON.parse(undo);
        changed();
        return;
      }
      if (!commit || !target) return;
      const link = pending ? linkSnapshot() : null;
      if (pending) shot.duration_seconds = String(PLACE_SECONDS);
      const reordered = moveShot(shot.id, target.beforeId);
      if (pending) applyLink(link);
      if (pending || reordered) { remember(undo); commitTimeline(shot.id, reordered); }
    };
    view.addEventListener('pointerup', () => finish(true));
    view.addEventListener('pointercancel', () => finish(false));
    view.addEventListener('keydown', event => {
      if (event.key === 'Escape' && dragging) finish(false);
    });
  }

  /* 拖镜头边缘。右缘：改本镜时长，后面的镜头顺移。左缘：默认移动与上一镜的切点（两镜此消彼长，总长不变）；
     按住 Alt 或第一镜时只改本镜时长。每次都从拖动开始时的状态重算，中途松开或按下 Alt 都不会累积误差。 */
  function resizeShot(drag, dx, alone) {
    const {shot, prev, cutAt, pps} = drag, MIN = 0.1;
    shot.duration_seconds = String(drag.length);
    if (prev) prev.duration_seconds = String(drag.prevLength);
    const raw = dx / pps * US;
    if (drag.resize === 'start' && prev && !alone) {
      const cut = settle(cutAt + raw, [0], drag.targets, pps) / US;
      const from = cutAt / US - drag.prevLength, to = cutAt / US + drag.length;
      const at = Math.min(to - MIN, Math.max(from + MIN, Math.round(cut * 10) / 10));
      prev.duration_seconds = String(snap(at - from));
      shot.duration_seconds = String(snap(to - at));
      applyLink(drag.link, true);   // 移动切点不改总长，其他轨道留在原处（从“只改本镜”切回来时也放回去）
      return;
    }
    const end = drag.resize === 'start' ? cutAt + Math.round(drag.length * US) - raw
      : settle(cutAt + Math.round(drag.length * US) + raw, [0], drag.targets, pps);
    shot.duration_seconds = String(snap((end - cutAt) / US));
    applyLink(drag.link);
  }

  /* 键盘替代拖动：Alt+←/→ 与相邻片段交换顺序，Shift+←/→ 时长 ±0.1 秒。 */
  function nudgeClip(event) {
    const node = event.target;
    if (!state.editing || node.parentElement.id !== 'track') return false;
    if (mainLocked() && (event.altKey || event.shiftKey) && {ArrowLeft: 1, ArrowRight: 1}[event.key]) { status(LOCKED); return true; }
    const step = {ArrowLeft: -1, ArrowRight: 1}[event.key];
    if (!step || event.altKey === event.shiftKey) return false;
    const shot = shotById(node.dataset.id), placed = plan().placed.map(item => item.shot.id);
    const index = placed.indexOf(shot.id);
    if (event.shiftKey) {
      const link = linkSnapshot();
      remember(JSON.stringify(state.working));
      shot.duration_seconds = String(snap(seconds(shot) + step * 0.1));
      applyLink(link);
      commitTimeline(shot.id, false);
    } else if (placed[index + step] !== undefined) {
      moveShot(shot.id, step < 0 ? placed[index - 1] : placed[index + 2] || null);
      commitTimeline(shot.id, true);
    }
    return true;
  }

  /* ---------- 节奏预览：按真实时长播放静态图，二维裁切运镜与硬切，与 layout 预演工具一致 ---------- */
  const preview = {time: 0, playing: false, last: 0, images: new Map(), current: null};
  const ASPECT_PATTERN = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/;
  const validAspect = value => value === '' || ASPECT_PATTERN.test(value) && value.split(':').every(part => Number(part) > 0);
  const clock = value => String(Math.floor(value / 60)).padStart(2, '0') + ':' + (value % 60).toFixed(1).padStart(4, '0');

  function aspectRatio() {
    const value = state.working.layout_aspect || '';
    return value && validAspect(value) ? value.split(':').reduce((width, height) => Number(width) / Number(height)) : 16 / 9;
  }

  function imageFor(shot) {
    const url = thumbUrl(shot.thumbnail);   // 缩略图未就绪就不载入，监视器显示占位
    if (!url) return null;
    let img = preview.images.get(url);
    if (!img) {
      img = new Image();
      img.addEventListener('load', drawPreview);
      img.addEventListener('error', drawPreview);
      img.src = url;
      preview.images.set(url, img);
    }
    return img.complete && img.naturalWidth ? img : null;
  }

  /* ---------- 媒体池：每个素材一个 <video>，叠在监视器画布上；播放位置以时钟为准，媒体跟随 ----------
     原生环境由宿主把素材所在文件夹映射为只读主机（resolveMedia），页面只拿到地址；预览环境读 preview-assets。 */
  const SYNC_TOLERANCE = 0.15;   // 播放中媒体与时钟相差超过这么多秒才重新定位，避免频繁跳帧
  const media = {urls: new Map(), elements: new Map(), pending: new Set(), picking: null, request: 0, live: false};

  function mediaUrl(source) {
    if (!host) return 'preview-assets/' + encodeURIComponent(baseName(source));
    return media.urls.get(source);   // undefined：尚未解析；''：文件不存在或格式不支持
  }

  function mediaElement(source) {
    const url = mediaUrl(source);
    if (!url) return null;
    let node = media.elements.get(source);
    if (!node) {
      node = el('video', {class: 'monitor-video', preload: 'auto', playsinline: true, hidden: true, 'aria-hidden': 'true'});
      node.addEventListener('loadedmetadata', refreshMedia);
      node.addEventListener('error', refreshMedia);
      node.src = url;
      media.elements.set(source, node);
      $('monitor-media').append(node);
    }
    return node;
  }

  /* 素材问题的说明；没有问题或尚在解析时返回 null。 */
  function mediaProblem(source) {
    const url = mediaUrl(source);
    if (url === '') return '找不到文件或格式不支持：' + source;
    const node = url ? mediaElement(source) : null;
    return node && node.error ? '无法解码这个视频：' + source : null;
  }

  const mediaDuration = source => {
    const node = mediaElement(source);
    return node && node.readyState >= 1 && Number.isFinite(node.duration) ? node.duration : null;
  };

  function whenMetadata(source) {
    return new Promise(resolve => {
      const node = mediaElement(source);
      if (!node || node.error) { resolve(null); return; }
      if (node.readyState >= 1) { resolve(node.duration); return; }
      node.addEventListener('loadedmetadata', () => resolve(node.duration), {once: true});
      node.addEventListener('error', () => resolve(null), {once: true});
    });
  }

  function resolveMedia() {
    if (!host || !media.live || !timeline()) return;   // 宿主收到 ready 之前会丢弃其他消息
    const sources = [...new Set([...timeline().video, ...timeline().audio].flatMap(track =>
      track.clips.flatMap(clip => [clip.source, ...(clip.takes || []).map(take => take.source)])))]
      .filter(source => !media.urls.has(source) && !media.pending.has(source));
    if (!sources.length) return;
    sources.forEach(source => media.pending.add(source));
    host.postMessage({type: 'resolveMedia', sources});
  }

  function onMediaUrls(urls) {
    for (const [source, url] of Object.entries(urls || {})) { media.urls.set(source, url); media.pending.delete(source); }
    refreshMedia();
  }

  /* 素材地址或元数据变化后，原地刷新覆盖条、视频块和监视器。 */
  function refreshMedia() {
    for (const node of $('track').querySelectorAll('.clip[data-id]')) {
      const shot = shotById(node.dataset.id), old = node.querySelector('.clip-video'), next = shot ? videoCover(shot) : null;
      if (old) old.remove();
      if (next) node.querySelector('.clip-label').after(next);
    }
    const block = $('detail').querySelector('.block-video'), shot = shotById(state.selectedId);
    if (block && shot && !block.contains(document.activeElement)) block.replaceWith(videoBlock(shot));
    drawPreview();
  }

  /* 当前时刻在主轨上应显示的视频：{node, local, clip}；镜头格内视频已结束时为空缺 {gap: true}；没有关联或素材不可用时为 null。 */
  function activeVideo(item, time) {
    const clip = item ? mainClip(item.shot) : null;
    const node = clip && !mediaProblem(clip.source) ? mediaElement(clip.source) : null;
    if (!node) return null;
    const offset = time - item.start;
    return offset >= clipSeconds(clip) ? {gap: true} : {node, clip, local: clip.in_us / US + offset};
  }

  /* 此刻要出声或出画的全部素材：主轨按镜头格，自由视频轨与音频轨按片段位置。
     画面只给最上面一条没关画面、此刻有片段的视频轨（主轨最下，自由轨序号越大越靠上）；声音各轨按自己的开关与音量同时播放。 */
  function activeMedia(main, time) {
    const t = timeline();
    if (!t) return [];
    const list = [], at = time * US;
    if (main && main.node) list.push({node: main.node, local: main.local, track: t.video[0], audio: main.clip.audio, picture: true});
    const free = (track, picture) => {
      const clip = track.clips.find(item => at >= item.start_us && at < item.start_us + item.out_us - item.in_us);
      const node = clip && !mediaProblem(clip.source) ? mediaElement(clip.source) : null;
      if (node) list.push({node, local: (clip.in_us + at - clip.start_us) / US, track, audio: clip.audio !== false, picture});
    };
    t.video.slice(1).forEach(track => free(track, true));
    t.audio.forEach(track => free(track, false));
    const shown = list.filter(entry => entry.picture && !entry.track.hidden).pop();
    for (const entry of list) {
      entry.visible = entry === shown;
      entry.muted = !entry.audio || entry.track.muted;
    }
    return list;
  }

  /* 让素材跟随时钟：暂停时逐帧定位，播放中偏差过大才重新定位；既不出画也不出声的不播放。 */
  function syncMedia(entries) {
    const live = new Set(entries.filter(entry => entry.visible || !entry.muted).map(entry => entry.node));
    for (const node of media.elements.values()) {
      if (live.has(node)) continue;
      node.hidden = true;
      if (!node.paused) node.pause();
    }
    for (const entry of entries) {
      const node = entry.node;
      if (!live.has(node)) continue;
      node.hidden = !entry.visible;
      node.muted = entry.muted;
      node.volume = entry.track.gain;
      const drift = Math.abs(node.currentTime - entry.local);
      if (preview.playing) {
        if (node.paused || drift > SYNC_TOLERANCE) node.currentTime = entry.local;
        if (node.paused) node.play().catch(() => {});
      } else {
        if (!node.paused) node.pause();
        if (drift > 0.01) node.currentTime = entry.local;
      }
    }
  }

  /* 监视器按画幅取可用区域内最大的等比尺寸，由外层居中。 */
  function sizeMonitor() {
    const stage = $('monitor-stage'), frame = $('monitor-frame'), ratio = aspectRatio();
    const width = Math.floor(Math.min(stage.clientWidth, stage.clientHeight * ratio));
    frame.style.inlineSize = width + 'px';
    frame.style.blockSize = Math.floor(width / ratio) + 'px';
  }

  function placePlayhead() {
    const total = span();
    // 竖线贯穿全部轨道；三角头画在吸顶的标尺行里，纵向滚动时也看得到
    for (const node of [$('playhead'), $('playhead-head')]) {
      node.hidden = total <= 0;
      node.style.left = trackX(Math.min(preview.time, total)) + 'px';
    }
  }

  /* 播放时播放头走出可见区就翻页，与剪辑软件一致。 */
  function followPlayhead() {
    const scroller = $('timeline-scroll'), x = trackX(Math.min(preview.time, span()));
    if (x < scroller.scrollLeft + layout.gutter || x > scroller.scrollLeft + scroller.clientWidth) {
      scroller.scrollLeft = x - layout.gutter - 16;
    }
  }

  /* 没有参考画面时画“合成占位”：镜号与提示文字，不冒充画面。 */
  function placeholder(ctx, shot, width, height, color, scale) {
    ctx.strokeStyle = color; ctx.globalAlpha = 0.4; ctx.setLineDash([6 * scale, 6 * scale]); ctx.lineWidth = scale;
    ctx.strokeRect(16 * scale, 16 * scale, width - 32 * scale, height - 32 * scale);
    ctx.globalAlpha = 1; ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = '600 ' + 32 * scale + 'px "Cascadia Mono", Consolas, monospace';
    ctx.fillText(shot.number, width / 2, height / 2 - 16 * scale);
    ctx.font = 14 * scale + 'px "Segoe UI", "Microsoft YaHei UI", sans-serif';
    ctx.fillText('合成占位 · 暂无参考画面', width / 2, height / 2 + 20 * scale);
  }

  /* 监视器上的字幕叠加：取最上面一条未静音字幕轨里覆盖当前时间的条目；多条同时出现时只显示最上的。 */
  function showSubtitle(time) {
    const node = $('monitor-subtitle'), t = timeline();
    const track = t && t.subtitles.find(item => !item.hidden);
    const cue = track && track.cues.find(item => time >= item.start_us / US && time < item.end_us / US);
    node.hidden = !cue;
    node.textContent = cue ? cue.text : '';
  }

  function drawPreview() {
    const {placed, total: planned} = plan();
    const total = span();
    const time = Math.min(preview.time, total);
    showSubtitle(time);
    // 镜头计划结束后（被自由轨素材撑出来的部分）没有镜头，画面留黑，时间码继续走
    const item = time < planned ? placed.find(entry => time < entry.start + entry.length) || placed[placed.length - 1] || null : null;
    $('timecode').textContent = clock(time);
    $('timecode-total').textContent = clock(total);
    placePlayhead();
    if (preview.playing) followPlayhead();
    $('monitor-shot').textContent = item ? item.shot.number : '';
    $('monitor-text').textContent = item ? item.shot.content || UNKNOWN
      : planned > 0 && total > planned ? '镜头之外：只有自由轨素材与字幕。' : '没有可预览的镜头：先确认时长。';
    const id = item ? item.shot.id : null;
    preview.current = id;

    // 关联了视频的镜头直接播放视频（不套预览运镜）；视频已结束的空缺保持黑场。监视器隐藏时也要同步，保证离开视图即停
    const video = activeVideo(item, time), mainHidden = Boolean(timeline() && timeline().video[0].hidden);
    syncMedia(activeMedia(video, time));

    const frame = $('monitor-frame'), canvas = $('monitor-canvas'), scale = window.devicePixelRatio || 1;
    const width = Math.round(frame.clientWidth * scale), height = Math.round(frame.clientHeight * scale);
    if (!width || !height) return;
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext('2d'), style = getComputedStyle(frame);
    ctx.fillStyle = style.backgroundColor;
    ctx.fillRect(0, 0, width, height);
    if (video) { canvas.setAttribute('aria-label', item.shot.number + (video.gap ? ' 视频空缺' : ' 视频')); return; }
    if (!item || mainHidden) return;   // 主轨关了画面：镜头的静态预演也不画
    const img = imageFor(item.shot);
    canvas.setAttribute('aria-label', item.shot.number + ' ' + (img ? '参考画面' : '暂无参考画面'));
    if (!img) { placeholder(ctx, item.shot, width, height, style.color, scale); return; }
    // 裁切框为相对原图的 [左, 上, 右, 下]；linear 在镜内由起始框线性变到结束框，裁切后按比例放入画幅、不足处留黑
    const camera = item.shot.layout_camera, full = [0, 0, 1, 1];
    const from = camera ? camera.start_crop : full, to = camera && camera.type === 'linear' ? camera.end_crop : from;
    const progress = Math.min(1, Math.max(0, (time - item.start) / item.length));
    const box = from.map((value, index) => value + (to[index] - value) * progress);
    const sw = (box[2] - box[0]) * img.naturalWidth, sh = (box[3] - box[1]) * img.naturalHeight;
    const fit = Math.min(width / sw, height / sh);
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, box[0] * img.naturalWidth, box[1] * img.naturalHeight, sw, sh,
      (width - sw * fit) / 2, (height - sh * fit) / 2, sw * fit, sh * fit);
  }

  function playButton() {
    // 按钮里固定是图标、文字与右侧的时间码；时间码由 drawPreview 更新，这里只换图标和文字
    $('play-toggle').querySelector('use').setAttribute('href', preview.playing ? '#i-pause' : '#i-play');
    $('play-label').textContent = preview.playing ? '暂停' : '播放';
    $('play-toggle').disabled = span() <= 0;
  }

  function setPlaying(on) {
    const total = span();
    if (on && total <= 0) return;
    if (on && preview.time >= total) preview.time = 0;
    preview.playing = on;
    playButton();
    if (on) { preview.last = performance.now(); requestAnimationFrame(tick); }
    else drawPreview();   // 让视频随之暂停
  }

  function tick(now) {
    if (!preview.playing) return;
    const total = span();
    preview.time += (now - preview.last) / 1000;
    preview.last = now;
    if (preview.time >= total) { preview.time = total; setPlaying(false); }
    drawPreview();
    if (preview.playing) requestAnimationFrame(tick);
  }

  function seek(time) {
    preview.time = Math.min(Math.max(0, time), span());
    drawPreview();
  }

  const seekAt = clientX => {
    if (layout.pps) seek((clientX - $('timeline').getBoundingClientRect().left - layout.gutter) / layout.pps);
  };

  function renderMonitor() {
    const value = state.working.layout_aspect || '', trigger = $('aspect-trigger');
    $('aspect-value').textContent = value || '16:9';
    trigger.setAttribute('aria-disabled', String(!state.editing));
    trigger.setAttribute('aria-invalid', String(!validAspect(value)));
    if (!state.editing) finishCustomAspect(false, false);
    preview.current = undefined;
    sizeMonitor();
    playButton();
    drawPreview();
  }

  function setupMonitor() {
    $('play-toggle').addEventListener('click', () => setPlaying(!preview.playing));
    // 在标尺上按住拖动播放头，画面实时跟随
    $('ruler').addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      setPlaying(false);
      seekAt(event.clientX);
      followPointer($('ruler'), event, move => seekAt(move.clientX));
    });
    $('track').addEventListener('click', event => { if (event.target === $('track')) seekAt(event.clientX); });
    new ResizeObserver(() => { sizeMonitor(); drawPreview(); }).observe($('monitor-stage'));
    // 时间线视图里空格总是播放或暂停，不再触发当前聚焦的按钮；文字输入、菜单与对话框除外。
    // 捕获阶段处理，先于按钮的默认动作与其他快捷键。
    const typing = target => target.closest('input, textarea, select, [contenteditable], [role="menu"], dialog');
    const playKey = event => event.key === ' ' && state.view === 'timeline'
      && !event.ctrlKey && !event.altKey && !event.metaKey && !typing(event.target);
    document.addEventListener('keydown', event => {
      if (!playKey(event)) return;
      event.preventDefault();
      event.stopPropagation();
      if (!event.repeat) setPlaying(!preview.playing);
    }, true);
    document.addEventListener('keyup', event => { if (playKey(event)) event.preventDefault(); }, true);
    // = / - 缩放，\ 适配全部（Premiere 键位）
    document.addEventListener('keydown', event => {
      if (state.view !== 'timeline' || event.ctrlKey || event.altKey || event.metaKey || typing(event.target)) return;
      const zoomKey = {'=': () => zoomBy(1.5), '+': () => zoomBy(1.5), '-': () => zoomBy(1 / 1.5), '\\': zoomFit,
        m: addMarker, M: addMarker, s: toggleSnap, S: toggleSnap}[event.key];
      if (zoomKey) { event.preventDefault(); zoomKey(); }
    });
  }

  /* ---------- 画幅：常用比例菜单，或自定义“宽:高”；留空表示未决定，预览按 16:9 ---------- */
  const ASPECTS = ['16:9', '9:16', '4:3', '3:4', '1:1', '4:5', '21:9', '2.39:1'];

  function setAspect(value) {
    if (value === (state.working.layout_aspect || '')) return;
    state.working.layout_aspect = value;
    changed();
  }

  function editCustomAspect() {
    const input = $('aspect-input');
    input.value = state.working.layout_aspect || '';
    input.removeAttribute('aria-invalid');
    $('aspect-trigger').hidden = true;
    input.hidden = false;
    input.focus();
    input.select();
  }

  /* 回车确认、Esc 放弃；失焦时合法就确认，不合法直接放弃。refocus 为真时焦点回到画幅按钮。 */
  function finishCustomAspect(commit, refocus) {
    const input = $('aspect-input'), value = input.value.trim();
    if (input.hidden) return;
    if (commit && !validAspect(value)) {
      if (refocus) { input.setAttribute('aria-invalid', 'true'); status('画幅应写成“宽:高”，例如 2.39:1。'); return; }
      commit = false;
    }
    input.hidden = true;
    $('aspect-trigger').hidden = false;
    if (refocus) $('aspect-trigger').focus();
    if (commit) setAspect(value);
  }

  function setupAspect() {
    const trigger = $('aspect-trigger'), input = $('aspect-input');
    trigger.addEventListener('click', () => {
      if (!state.editing) return;
      const value = state.working.layout_aspect || '';
      openMenu({trigger, align: 'end', label: '画幅', restore: () => trigger.focus(), items: [
        ...ASPECTS.map(aspect => ({label: aspect, checked: value === aspect, pick: () => { setAspect(aspect); trigger.focus(); }})),
        {label: '自定义…', checked: value !== '' && !ASPECTS.includes(value), pick: editCustomAspect}
      ]});
    });
    input.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); finishCustomAspect(true, true); }
      else if (event.key === 'Escape') { event.preventDefault(); finishCustomAspect(false, true); }
    });
    input.addEventListener('blur', () => finishCustomAspect(true, false));
  }

  /* ---------- 时间线说明：问号按钮展开，点外面、Esc 或离开时间线视图时收起 ---------- */
  function showHelp(value) {
    $('help-panel').hidden = !value;
    $('help-trigger').setAttribute('aria-expanded', String(value));
    if (value) place($('help-panel'), $('help-trigger'), 'start');
  }

  function setupHelp() {
    const trigger = $('help-trigger'), panel = $('help-panel');
    trigger.addEventListener('click', () => showHelp(panel.hidden));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !panel.hidden) { showHelp(false); trigger.focus(); }
    });
    document.addEventListener('pointerdown', event => {
      if (!panel.hidden && !panel.contains(event.target) && !trigger.contains(event.target)) showHelp(false);
    });
  }

  /* ---------- 渲染：侧边栏（只放表格里没有的信息） ---------- */
  function assetList(shot) {
    const {assets, issues} = shotRefs(shot);
    if (!assets.length && !issues.length) return el('p', {class: 'field-empty', text: '描述和提示词里还没有引用资产。'});
    return el('ul', {class: 'asset-list'}, [
      ...assets.map(asset => {
        const url = thumbUrl(asset.path);
        return el('li', {}, el('button', {type: 'button', class: 'asset-card', 'data-asset': asset.id,
          'aria-label': asset.id + ' ' + asset.name + '，复制图片'}, [
          url ? el('img', {class: 'asset-thumb', src: url, alt: ''}) : el('span', {class: 'asset-thumb thumb-empty', 'data-thumb-asset': imageUrl(asset.path) ? asset.id : null}, icon('image')),
          el('span', {class: 'mono', text: asset.id}),
          el('span', {class: 'asset-name', text: asset.name})
        ]));
      }),
      ...issues.map(([token, issue]) => el('li', {class: 'asset-issue'}, [
        icon('alert'), el('span', {class: 'mono', text: token}), el('span', {text: issue})
      ]))
    ]);
  }

  function assetsBlock(shot) {
    return el('section', {class: 'block block-assets'}, [
      el('h3', {class: 'block-title'}, [el('span', {text: '关联资产'}), el('span', {class: 'count', text: shotRefs(shot).assets.length + ' 项'})]),
      assetList(shot)
    ]);
  }

  /* ---------- 镜头的视频：关联、替换与声音都在镜头上下文里完成 ----------
     已关联时只显示一行：缩略图、文件名与声音开关；裁剪与入出点在时间线上做，这里不再重复。
     只读与编辑共用同一套元素（解锁不改布局）：空状态是一个虚线按钮，次要操作收在标题旁的菜单里。 */
  function videoBlock(shot) {
    const clip = mainClip(shot), editing = state.editing && !mainLocked();
    const title = el('h3', {class: 'block-title'}, [el('span', {text: '视频'})]);
    const block = el('section', {class: 'block block-video'}, title);
    if (!clip) {
      block.append(el('button', {type: 'button', class: 'video-empty', 'aria-disabled': editing ? null : 'true', tabindex: editing ? null : '-1',
        onclick: () => { if (editing) pickVideo(shot); }}, [icon('film'), el('span', {text: editing ? '关联视频…' : '未关联视频'})]));
      return block;
    }
    const more = el('button', {type: 'button', class: 'btn btn-quiet btn-icon video-more', title: '视频操作', 'aria-haspopup': 'menu',
      'aria-expanded': 'false', 'aria-disabled': editing ? null : 'true', onclick: event => { if (editing) openVideoMenu(event.currentTarget, shot); }},
      [icon('more'), el('span', {class: 'visually-hidden', text: '视频操作'})]);
    title.append(more);
    const versions = versionsOf(clip);
    block.append(el('div', {class: 'video-versions' + (versions.length > 1 ? ' is-multi' : ''), role: 'group', 'aria-label': '视频版本'},
      versions.map(version => versionRow(shot, clip, version, editing))));
    if (editing) block.append(el('button', {type: 'button', class: 'video-add', onclick: () => pickVideo(shot)}, [icon('plus'), el('span', {text: '添加版本…'})]));
    return block;
  }

  /* 一个版本一行：点左边（缩略图与文件名）切换为当前使用；当前版本带声音开关，解锁后每行都可移除。
     缩略图取入点那一帧；悬停整行时在浮层里静音循环播放这个版本的采用范围。 */
  function versionRow(shot, clip, version, editing) {
    const active = version.id === clip.id, problem = mediaProblem(version.source), url = mediaUrl(version.source);
    const thumb = url ? thumbVideo(url + '#t=' + (version.in_us / US).toFixed(3)) : icon(problem ? 'alert' : 'film');
    const pick = el('button', {type: 'button', class: 'video-pick', 'aria-pressed': String(active), title: problem || version.source,
      'aria-disabled': editing && !active ? null : 'true', onclick: () => { if (editing && !active) useVersion(shot, version.id); }},
      [el('span', {class: 'video-thumb-box'}, thumb), el('span', {class: 'video-name', text: baseName(version.source)})]);
    const remove = editing ? el('button', {type: 'button', class: 'btn btn-quiet btn-icon video-remove', title: '移除这个版本（文件不受影响）',
      onclick: () => removeVersion(shot, version.id)}, [icon('trash'), el('span', {class: 'visually-hidden', text: '移除这个版本'})]) : null;
    return el('div', {class: 'video-file' + (active ? ' is-active' : '') + (problem ? ' is-missing' : ''),
      'data-peek-video': url ? shot.id + '|' + version.id : null}, [pick, active ? soundButton(clip) : null, remove]);
  }

  /* 镜头的全部视频版本：当前版本（片段顶层字段）加上 takes，按文件名自然排序（v001、v002…），界面上的序号都按这个顺序。 */
  const VERSION_FIELDS = ['id', 'source', 'in_us', 'out_us', 'audio'];
  const versionsOf = clip => [Object.fromEntries(VERSION_FIELDS.map(key => [key, clip[key]])), ...clip.takes]
    .sort((a, b) => baseName(a.source).localeCompare(baseName(b.source), 'zh', {numeric: true}));

  /* 切换版本：把选中的版本与顶层字段（连同 id）互换，每个版本的 id 始终跟着同一个文件。 */
  function useVersion(shot, id) {
    const clip = mainClip(shot), take = clip && clip.takes.find(item => item.id === id);
    if (!take || mainLocked()) return;
    editLane(shot.number + ' 改用 ' + baseName(take.source) + '。', () => {
      for (const key of VERSION_FIELDS) [clip[key], take[key]] = [take[key], clip[key]];
    });
    refreshVideoBlock(shot);
  }

  /* 移除一个版本；移除的是当前版本时，按顺序改用剩下的第一个；最后一个也移除就是取消关联。素材文件不受影响。 */
  function removeVersion(shot, id) {
    const clip = mainClip(shot);
    if (!clip || mainLocked()) return;
    const name = baseName(versionsOf(clip).find(version => version.id === id).source);
    editLane(shot.number + ' 已移除版本 ' + name + '；素材文件不受影响。', () => {
      if (clip.id !== id) { clip.takes.splice(clip.takes.findIndex(take => take.id === id), 1); return; }
      const next = versionsOf(clip).find(version => version.id !== id);
      if (!next) { const clips = timeline().video[0].clips; clips.splice(clips.indexOf(clip), 1); return; }
      clip.takes.splice(clip.takes.indexOf(clip.takes.find(take => take.id === next.id)), 1);
      for (const key of VERSION_FIELDS) clip[key] = next[key];
    });
    refreshVideoBlock(shot);
  }

  /* 点版本时焦点在详情栏里，syncDetail 不会重画，这里只换掉视频块。 */
  function refreshVideoBlock(shot) {
    const old = $('detail').querySelector(':scope > .block-video');
    if (old && shot.id === state.selectedId) old.replaceWith(videoBlock(shot));
  }

  /* 详情栏每次重绘都会新建元素；视频缩略图要重新加载、会闪一下，所以按地址缓存同一个 <video> 反复用。 */
  const thumbVideos = new Map();
  function thumbVideo(src) {
    let node = thumbVideos.get(src);
    if (!node) {
      node = el('video', {class: 'video-thumb', src, preload: 'metadata', muted: true, playsinline: true, tabindex: '-1', 'aria-hidden': 'true'});
      thumbVideos.set(src, node);
    }
    return node;
  }

  /* 声音开关：点击时焦点在按钮上，refreshMedia 不会重建视频块，所以按钮自己换成新状态并保留焦点。 */
  function soundButton(clip) {
    const editing = state.editing;
    const button = el('button', {type: 'button', class: 'btn btn-quiet btn-icon', 'aria-pressed': String(clip.audio),
      title: clip.audio ? '使用视频声音' : '不使用视频声音', 'aria-disabled': editing ? null : 'true', tabindex: editing ? null : '-1',
      onclick: () => {
        if (!state.editing) return;
        clip.audio = !clip.audio;
        changed(); refreshMedia();
        const next = soundButton(clip);
        button.replaceWith(next);
        next.focus();
      }},
      [icon(clip.audio ? 'volume' : 'mute'), el('span', {class: 'visually-hidden', text: '使用视频声音'})]);
    return button;
  }

  function openVideoMenu(trigger, shot) {
    openMenu({trigger, align: 'end', label: '视频操作', restore: () => trigger.focus(), items: videoItems(shot)});
  }

  /* 视频操作，详情栏的“⋯”与右键镜头共用：有多个版本时先列出版本（当前的打勾），再是添加、对齐时长与移除。 */
  function videoItems(shot) {
    const clip = mainClip(shot);
    if (!state.editing || mainLocked()) return [];
    const versions = clip ? versionsOf(clip) : [];
    return [
      ...(versions.length > 1 ? versions.map((version, index) => ({label: (index + 1) + '/' + versions.length + '  ' + baseName(version.source),
        checked: version.id === clip.id, icon: icon('film'), pick: () => useVersion(shot, version.id)})) : []),
      {label: clip ? '添加版本…' : '关联视频…', icon: icon('plus'), pick: () => pickVideo(shot)},
      clip ? {label: '用视频长度设为镜头时长', icon: icon('film'), pick: () => fitShotToVideo(shot)} : null,
      clip ? {label: versions.length > 1 ? '移除全部版本' : '移除视频', icon: icon('trash'), pick: () => {
        editLane(shot.number + ' 已移除视频关联；素材文件不受影响。', () => {
          const clips = timeline().video[0].clips;
          clips.splice(clips.indexOf(clip), 1);
        });
        renderDetail();
      }} : null
    ].filter(Boolean).map(item => Object.assign({checked: false}, item));
  }

  /* 镜头时长只精确到 0.1 秒：取不超过采用长度的 0.1 秒整，出点随之收齐，镜头格与视频正好等长。 */
  function fitShotToVideo(shot) {
    const clip = mainClip(shot), link = linkSnapshot();
    const length = Math.max(0.1, Math.floor(clipSeconds(clip) * 10 + 1e-6) / 10);
    shot.duration_seconds = tenth(length);
    clip.out_us = clip.in_us + Math.round(length * US);
    applyLink(link);
    changed(); renderTable(); renderDetail();
    status(shot.number + ' 的时长已设为 ' + tenth(length) + ' 秒（取视频采用长度的 0.1 秒整）。');
  }

  /* 用系统对话框选视频；读到素材长度后才关联，入点为 0，出点取素材长度与镜头时长中较短的一个。 */
  function pickVideo(shot) {
    if (!host) { status('预览模式：没有连接宿主程序，不能选择文件。'); return; }
    media.picking = {request: ++media.request, shotId: shot.id};
    host.postMessage({type: 'pickMedia', kind: 'video', multiple: true, request: media.request});
  }

  /* 找本轨在 from 之后第一个放得下的位置：与已有片段不重叠即可，不挤走别人。 */
  function freeStart(track, fromUS, lengthUS) {
    const spans = (track.cues || track.clips).map(item => spanOf(item, track.cues ? 'subtitle' : 'clip')).sort((a, b) => a.start - b.start);
    let start = fromUS;
    for (const span of spans) {
      if (span.end <= start) continue;
      if (span.start >= start + lengthUS) break;
      start = span.end;
    }
    return start;
  }

  async function onMediaPicked(data) {
    const picking = media.picking;
    if (!picking || data.request !== picking.request) return;
    media.picking = null;
    if (data.status === 'cancelled') return;
    if (data.status !== 'picked') { status('这个文件不能作为视频使用：只支持 mp4、m4v、mov、webm。'); return; }
    if (picking.shotId) { addVersions(picking.shotId, data.items || [{source: data.source, url: data.url}]); return; }
    media.urls.set(data.source, data.url);
    const duration = await whenMetadata(data.source);
    if (!state.editing) return;
    // 插入：没有 shotId，只有目标轨，素材放到播放头之后第一个放得下的空档
    if (picking.target) {
      const found = findTrack(picking.target);
      if (!found || !duration) { status('无法解码这个素材，未插入。'); return; }
      const length = Math.round(duration * US), isVideo = found.kind === 'video';
      const start = freeStart(found.track, picking.startUs, length), clipId = newTimelineId('c');
      editLane('已插入 ' + baseName(data.source) + '：' + round(start / US) + ' 秒起。', () => {
        found.track.clips.push(isVideo
          ? {id: clipId, start_us: start, source: data.source, in_us: 0, out_us: length, audio: true}
          : {id: clipId, start_us: start, source: data.source, in_us: 0, out_us: length});
        found.track.clips.sort((a, b) => a.start_us - b.start_us);
        state.timelineSel = {track: found.track.id, id: clipId};
      });
      return;
    }
  }

  /* 给镜头加视频版本：还没关联时第一个成为当前版本，其余（以及已关联时的全部）进候选，不自动切换。
     已经在版本里的文件跳过；读不出长度的视频不加。出点取素材长度与镜头时长中较短的一个。 */
  async function addVersions(shotId, items) {
    for (const item of items) media.urls.set(item.source, item.url);
    const durations = await Promise.all(items.map(item => whenMetadata(item.source)));
    const shot = shotById(shotId);
    if (!state.editing || !shot) return;
    let clip = mainClip(shot);
    const known = new Set(clip ? versionsOf(clip).map(version => version.source) : []);
    const fresh = items.map((item, index) => ({source: item.source, duration: durations[index]}))
      .filter(item => item.duration && !known.has(item.source));
    const skipped = items.length - fresh.length;
    if (!fresh.length) { status(skipped ? '没有可添加的视频：文件已在版本里，或无法解码。' : '没有选择视频。'); return; }
    const slot = seconds(shot), firstMedia = !state.working.timeline, linked = Boolean(clip);
    editLane('', () => {
      for (const item of fresh) {
        const version = {id: newTimelineId(clip ? 't' : 'c'), source: item.source, in_us: 0,
          out_us: Math.round(Math.min(item.duration, slot === null ? item.duration : slot) * US), audio: true};
        if (clip) clip.takes.push(version);
        else { clip = Object.assign(version, {shot_id: shot.id, takes: []}); ensureTimeline().video[0].clips.push(clip); }
      }
    });
    renderDetail();
    status(shot.number + (linked ? ' 已添加 ' + fresh.length + ' 个版本，在右侧点选即可改用。'
      : ' 已关联视频 ' + baseName(fresh[0].source) + (fresh.length > 1 ? '，另有 ' + (fresh.length - 1) + ' 个版本可选' : ''))
      + (skipped ? '（跳过 ' + skipped + ' 个已存在或无法解码的文件）' : '')
      + (firstMedia ? '；保存后文件升级为含时间线的新格式，旧版编辑器将无法打开。' : '。'));
  }

  /* 导入字幕或歌词：文件由宿主读取解析，页面只拿到条目；不在这里生成文字，也不自动对齐音乐。
     每次导入建一条新字幕轨，按条目顺序生成 ID，逐字时间原样保留。 */
  /* 手动新建一条空字幕：放进最上面的字幕轨（没有就先建一条）。播放头在空档里就从播放头起，长 2 秒但不越过下一条；
     播放头落在已有字幕里时顺延到下一个能放下 2 秒的空档。 */
  function addCue(target) {
    if (!state.editing) return;
    const t = ensureTimeline(), length = 2 * US;
    if (target && target.locked) { status(LOCKED); return; }
    editLane('已新建字幕：在右侧填写文字。', () => {
      let track = target || t.subtitles.find(item => !item.locked);
      if (!track) { track = {id: newTimelineId('s'), name: '', language: '', hidden: false, locked: false, cues: []}; t.subtitles.push(track); }
      const at = snapUs(preview.time * US);
      const inside = track.cues.some(cue => at >= cue.start_us && at < cue.end_us);
      const start = inside ? freeStart(track, at, length) : at;
      const next = track.cues.filter(cue => cue.start_us >= start).map(cue => cue.start_us);
      const end = inside ? start + length : Math.min(start + length, ...next);
      const cue = {id: newTimelineId('q'), start_us: start, end_us: end, text: '', words: []};
      track.cues.push(cue);
      track.cues.sort((a, b) => a.start_us - b.start_us);
      state.timelineSel = {track: track.id, id: cue.id};
    });
    focusDetailText();
  }

  function pickSubtitle() {
    if (!state.editing) return;
    if (!host) { status('预览模式：没有连接宿主程序，不能选择文件。'); return; }
    media.picking = {request: ++media.request, subtitle: true};
    host.postMessage({type: 'pickSubtitle', request: media.request});
  }

  function onSubtitlePicked(data) {
    const picking = media.picking;
    if (!picking || !picking.subtitle || data.request !== picking.request) return;
    media.picking = null;
    if (data.status === 'cancelled') return;
    if (data.status !== 'picked') { status(data.message || '这个文件不能作为字幕使用。'); return; }
    if (!state.editing) return;
    const firstMedia = !state.working.timeline, t = ensureTimeline();
    t.subtitles.push({
      id: newTimelineId('s'), name: '', language: data.language || '', hidden: false, locked: false,
      cues: data.cues.map(cue => ({id: newTimelineId('q'), start_us: cue.start_us, end_us: cue.end_us, text: cue.text, words: cue.words || []}))
    });
    changed(); renderTimeline();
    status('已导入 ' + (data.name || '字幕') + '：' + data.cues.length + ' 条' +
      (firstMedia ? '；保存后文件升级为含时间线的新格式，旧版编辑器将无法打开。' : '。'));
  }

  function detailNodes(shot) {
    if (!shot) return [el('h2', {id: 'detail-title', class: 'label', text: '镜头详情'}),
      el('p', {class: 'field-empty', text: '没有可显示的镜头。'})];
    const refresh = () => {
      changed();
      $('detail').querySelector('.block-assets').replaceWith(assetsBlock(shot));
      const cell = $('shot-rows').querySelector('[data-id="' + CSS.escape(shot.id) + '"] .td-assets');
      if (cell) cell.replaceChildren(...assetsCell(shot));
    };
    return [
      el('header', {class: 'detail-head'}, [
        el('p', {class: 'label', text: '镜头详情'}),
        el('h2', {class: 'detail-id', id: 'detail-title', text: shot.number})
      ]),
      textBlock('f-description', '详细描述', shot, 'description', refresh, DESCRIPTION_LIMIT),
      assetsBlock(shot),
      videoBlock(shot),
      textBlock('f-prompt', '图片生成提示词', shot, 'prompt', refresh),
      textBlock('f-video-prompt', '视频提示词', shot, 'video_prompt', refresh)
    ];
  }

  /* 切换镜头时旧内容作为残影淡出、新内容同时淡入；不延迟新内容的出现。 */
  /* 详情栏的对象：选中标记或字幕条目时显示它们，否则显示当前镜头。key 用来判断是否需要换内容。 */
  function detailSubject() {
    const marker = selectedMarker();
    if (marker) return {key: 'marker:' + marker.id, marker};
    const found = selectedItem();
    if (found && found.kind === 'subtitle') return {key: 'cue:' + found.item.id, cue: found.item, track: found.track};
    return {key: 'shot:' + state.selectedId};
  }

  function subjectNodes(subject) {
    if (subject.marker) return markerDetail(subject.marker);
    if (subject.cue) return cueDetail(subject.cue, subject.track);
    return detailNodes(shotById(state.selectedId));
  }

  /* 对象变了就换内容；refresh 时对象没变也重画（时间改了），但焦点在详情栏里时不打断输入。 */
  function syncDetail(refresh) {
    const detail = $('detail');
    if (detailSubject().key !== detail.dataset.subject) renderDetail(true);
    else if (refresh && !detail.contains(document.activeElement)) renderDetail();
  }

  const rangeText = (start, end) => end === null ? round(start / US) + ' 秒' : round(start / US) + '–' + round(end / US) + ' 秒';

  /* 字幕与标记的文字框：只读与编辑共用；获得焦点时拍快照，失焦时若有改动就压进撤销栈。 */
  function textField(id, title, value, onInput, locked) {
    let before = null;
    const editable = state.editing && !locked;
    const input = el('textarea', {id, class: 'cell-input detail-text', 'aria-labelledby': id + '-title', spellcheck: 'false',
      readonly: !editable, tabindex: editable ? null : '-1',
      onfocus: () => { before = JSON.stringify(state.working); },
      oninput: () => { onInput(input.value); changed(); },
      onchange: () => {
        if (!before) return;
        history.past.push(before);
        if (history.past.length > UNDO_LIMIT) history.past.shift();
        history.future.length = 0;
        before = JSON.stringify(state.working);
      }});
    input.value = value;
    return el('section', {class: 'block'}, [el('h3', {class: 'block-title', id: id + '-title'}, el('span', {text: title})), input]);
  }

  function focusDetailText() {
    // 切换时旧内容还在淡出残影里，只找正式内容里的输入框
    const input = $('detail').querySelector(':scope > .block > .detail-text');
    if (input && state.editing) input.focus();
  }

  function cueDetail(cue, track) {
    const code = 'S' + (timeline().subtitles.indexOf(track) + 1);
    return [
      el('header', {class: 'detail-head'}, [
        el('p', {class: 'label', text: '字幕 · ' + code}),
        el('h2', {class: 'detail-id', id: 'detail-title', text: rangeText(cue.start_us, cue.end_us)})
      ]),
      textField('f-cue', '文字', cue.text, value => { cue.text = value; }, track.locked),
      cue.words.length ? el('p', {class: 'detail-note', text: '含逐字时间 ' + cue.words.length + ' 段；改动文字不会改逐字时间。'}) : null
    ];
  }

  function markerDetail(marker) {
    return [
      el('header', {class: 'detail-head'}, [
        el('p', {class: 'label', text: (marker.end_us === null ? '标记' : '范围标记') + ' · ' + (marker.author === 'agent' ? 'Agent' : '用户')}),
        el('h2', {class: 'detail-id', id: 'detail-title', text: rangeText(marker.start_us, marker.end_us)})
      ]),
      textField('f-marker', '说明', marker.text, value => { marker.text = value; }, markersLocked())
    ];
  }

  function renderDetail(crossfade) {
    const detail = $('detail'), subject = detailSubject();
    detail.dataset.subject = subject.key;
    const old = [...detail.children].filter(node => !node.classList.contains('detail-ghost'));
    if (crossfade && old.length && !reducedMotion()) {
      detail.scrollTop = 0;
      const ghost = el('div', {class: 'detail-ghost', 'aria-hidden': 'true', inert: true}, old);
      ghost.addEventListener('animationend', () => ghost.remove(), {once: true});
      detail.querySelectorAll('.detail-ghost').forEach(node => node.remove());
      detail.replaceChildren(ghost, ...subjectNodes(subject));
      detail.classList.remove('entering'); void detail.offsetWidth; detail.classList.add('entering');
      return;
    }
    detail.replaceChildren(...subjectNodes(subject));
  }

  function render() {
    renderChrome(); renderTable(); renderTimeline(); renderDetail();
    resolveMedia();
  }

  /* ---------- 选择与视图 ---------- */
  function selectedNode() {
    const view = state.view === 'table' ? $('view-table') : $('view-timeline');
    return view.querySelector('[data-id="' + CSS.escape(state.selectedId) + '"]');
  }

  /* 只更新已有节点的选中标记，避免重建时丢失焦点。 */
  function markSelection() {
    // 只管表格行与镜头片段；选中了泳道条目或标记时，时间线上的镜头让出高亮，避免两处同时亮着
    for (const node of document.querySelectorAll('tr[data-id], .clip[data-id]')) {
      const selected = node.dataset.id === state.selectedId && (node.tagName === 'TR' || !state.timelineSel);
      if (node.tagName === 'TR') {
        node.setAttribute('aria-selected', String(selected));
        const number = node.querySelector('.cell-number');
        if (number && !state.editing) number.tabIndex = selected ? 0 : -1;
      } else {
        node.setAttribute('aria-pressed', String(selected));
        node.tabIndex = selected ? 0 : -1;
      }
    }
  }

  function select(id, focus) {
    const fromLane = Boolean(state.timelineSel);
    if (!shotById(id) || id === state.selectedId && !focus && !fromLane) return;
    const changedShot = id !== state.selectedId || fromLane;
    state.selectedId = id;
    state.timelineSel = null;
    markLaneSelection();
    markSelection();
    if (changedShot) renderDetail(true);
    const node = selectedNode();
    if (node) {
      node.scrollIntoView({block: 'nearest', inline: 'nearest'});
      if (focus) (node.querySelector('.cell-number') || node).focus();
    }
  }

  function moveSelection(delta) {
    const list = shots(), index = list.findIndex(shot => shot.id === state.selectedId);
    const next = list[Math.min(list.length - 1, Math.max(0, index + delta))];
    if (next) select(next.id, true);
  }

  function setView(view, focusTab) {
    if (view === state.view) return;
    state.view = view;
    for (const name of ['table', 'timeline']) {
      const tab = $('tab-' + name), active = name === view;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      $('view-' + name).hidden = !active;
    }
    if (focusTab) $('tab-' + view).focus();
    // 隐藏时量不到宽度，切回来按真实宽度重排一次
    if (view === 'timeline') { layoutTimeline(currentPps()); renderMonitor(); } else { setPlaying(false); showHelp(false); }
    const node = selectedNode();
    if (node) node.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }

  /* ---------- 弹出菜单：镜头标记与分段类别共用 ---------- */
  let openedMenu = null;

  function closeMenu(restoreFocus) {
    const menu = $('popup-menu');
    if (menu.hidden) return;
    menu.hidden = true;
    const {trigger, restore} = openedMenu;
    openedMenu = null;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus) restore();
  }

  /* trigger 是展开菜单的按钮（再点一次收起）；右键菜单没有 trigger，只给定位锚点。 */
  function openMenu({trigger, anchor, align, label, items, restore}) {
    const menu = $('popup-menu');
    const again = Boolean(trigger && openedMenu && openedMenu.trigger === trigger);
    closeMenu(again);
    if (again) return;
    openedMenu = {trigger, restore};
    menu.setAttribute('aria-label', label);
    menu.replaceChildren(...items.map(item => el('button', {
      type: 'button', role: 'menuitemradio', class: 'menu-item', tabindex: '-1',
      'aria-checked': String(item.checked), onclick: () => { closeMenu(false); item.pick(); }
    }, [item.icon, el('span', {text: item.label}), item.hint ? el('kbd', {text: item.hint}) : null])));
    menu.hidden = false;
    if (trigger) trigger.setAttribute('aria-expanded', 'true');
    place(menu, anchor || trigger, align);
    (menu.querySelector('[aria-checked="true"]') || menu.firstChild).focus();
  }

  function openMarkMenu(trigger, shot) {
    openMenu({trigger, align: 'end', label: '镜头标记', restore: () => trigger.focus(),
      items: [...MARKS, {key: '', label: '清除标记'}].map(mark => ({
        label: mark.label, checked: (shot.mark || '') === mark.key,
        icon: el('span', {class: 'mark mark-' + (mark.key || 'none')}, icon('mark-' + (mark.key || 'none'))),
        pick: () => {
          shot.mark = mark.key;
          changed(); renderTable(); renderTimeline();
          const next = $('shot-rows').querySelector('[data-id="' + CSS.escape(shot.id) + '"] .mark-btn');
          if (next) { next.focus(); pulse(next, 'just-set'); }
        }
      }))});
  }

  /* 添加轨道与素材：新轨道、导入字幕，或把视频/音频放到播放头处。 */
  function openTrackMenu(trigger) {
    openMenu({trigger, align: 'start', label: '添加轨道', restore: () => trigger.focus(), items: [
      {label: '视频轨', icon: icon('film'), pick: () => addTrack('video')},
      {label: '音频轨', icon: icon('volume'), pick: () => addTrack('audio')},
      {label: '在播放头新建字幕', icon: icon('text'), pick: addCue},
      {label: '导入字幕…', icon: icon('text'), pick: pickSubtitle},
      {label: '在播放头插入视频…', icon: icon('film'), pick: () => insertMedia('video')},
      {label: '在播放头插入音频…', icon: icon('volume'), pick: () => insertMedia('audio')}
    ]});
  }

  /* 把素材放到播放头：没有对应自由轨就先建一条；播放头位置放不下时顺延到本轨下一个空档。 */
  function insertMedia(kind, target) {
    if (!state.editing) return;
    if (!host) { status('预览模式：没有连接宿主程序，不能选择文件。'); return; }
    if (target && target.locked) { status(LOCKED); return; }
    const t = ensureTimeline(), free = () => (kind === 'video' ? t.video.slice(1) : t.audio).find(item => !item.locked);
    let track = target || free();
    if (!track) {
      addTrack(kind);
      track = free();
    }
    media.picking = {request: ++media.request, target: track.id, startUs: snapUs(preview.time * US)};
    host.postMessage({type: 'pickMedia', kind, request: media.request});
  }

  function addTrack(kind) {
    if (!state.editing) return;
    ensureTimeline()[kind].push(kind === 'video'
      ? {id: newTimelineId('v'), name: '', muted: false, hidden: false, locked: false, gain: 1, clips: []}
      : {id: newTimelineId('a'), name: '', muted: false, locked: false, gain: 1, clips: []});
    changed(); renderTimeline();
    status('已添加' + (kind === 'video' ? '视频' : '音频') + '轨。');
  }

  function setupMenu() {
    const menu = $('popup-menu');
    menu.addEventListener('keydown', event => {
      const items = [...menu.children], index = items.indexOf(document.activeElement);
      const next = {ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: items.length - 1}[event.key];
      if (next !== undefined) { event.preventDefault(); items[(next + items.length) % items.length].focus(); }
      else if (event.key === 'Escape') { event.preventDefault(); closeMenu(true); }
      else if (event.key === 'Tab') closeMenu(false);
    });
    document.addEventListener('pointerdown', event => {
      if (!menu.hidden && !menu.contains(event.target) && !(openedMenu.trigger && openedMenu.trigger.contains(event.target))) closeMenu(false);
    });
  }

  /* 描述与提示词的复制、剪切只放纯文字：染色只为方便阅读，分段标记与样式都不进剪贴板。 */
  function setupPlainCopy() {
    const plain = event => {
      const selection = getSelection();
      if (!selection.rangeCount || selection.isCollapsed) return;
      const node = selection.getRangeAt(0).commonAncestorContainer;
      const box = (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement).closest('.rich');
      if (!box) return;
      event.preventDefault();
      event.clipboardData.setData('text/plain', plainOf(selection.getRangeAt(0).cloneContents()));
      if (event.type === 'cut' && box.isContentEditable) document.execCommand('delete');
    };
    document.addEventListener('copy', plain);
    document.addEventListener('cut', plain);
  }

  /* 把浮层锚定到触发元素下方；空间不够时翻到上方、向内收。dataset.side 让进出动画朝向触发元素。 */
  function place(layer, anchor, align) {
    const rect = anchor.getBoundingClientRect(), box = layer.getBoundingClientRect(), gap = 4;
    let left = align === 'end' ? rect.right - box.width : rect.left;
    left = Math.max(gap, Math.min(left, window.innerWidth - box.width - gap));
    let top = rect.bottom + gap, side = 'below';
    if (top + box.height > window.innerHeight - gap) { top = Math.max(gap, rect.top - box.height - gap); side = 'above'; }
    layer.style.left = left + 'px';
    layer.style.top = top + 'px';
    layer.dataset.side = side;
  }

  /* ---------- 图片预览：素材引用悬停或聚焦时显示、点击复制；表格缩略图悬停时显示大图；视频缩略图悬停时静音循环播放采用范围 ---------- */
  function setupPeek() {
    const peek = $('asset-peek'), img = $('peek-img'), video = $('peek-video');
    let anchor = null, range = null;
    const assetFor = node => state.working.assets.find(asset => asset.id === node.dataset.asset);
    const stopVideo = () => {
      range = null;
      video.pause(); video.hidden = true;
      video.removeAttribute('src'); video.load();
    };
    const show = node => {
      if (anchor === node) return;
      const [shotId, versionId] = (node.dataset.peekVideo || '').split('|'), clipShot = shotId ? shotById(shotId) : null;
      const main = clipShot && mainClip(clipShot), clip = main ? versionsOf(main).find(version => version.id === versionId) || null : null;
      const shot = clip ? clipShot : node.dataset.peekShot ? shotById(node.dataset.peekShot) : null, asset = shot ? null : assetFor(node);
      if (!shot && !asset) return;
      anchor = node;
      stopVideo();
      img.hidden = Boolean(clip);
      if (clip) {
        range = {from: clip.in_us / US, to: clip.out_us / US};
        video.hidden = false;
        video.src = mediaUrl(clip.source);
        video.currentTime = range.from;
        video.play().catch(() => {});
      } else img.src = thumbUrl(shot ? shot.thumbnail : asset.path) || '';
      $('peek-id').textContent = shot ? shot.number : asset.id;
      $('peek-label').textContent = clip ? baseName(clip.source) : shot ? shot.content : asset.name;
      peek.classList.toggle('peek-large', Boolean(shot));
      peek.hidden = false;
      node.setAttribute('aria-describedby', 'asset-peek');
      place(peek, node, 'start');
    };
    const hide = () => {
      if (!anchor) return;
      anchor.removeAttribute('aria-describedby');
      anchor = null; peek.hidden = true;
      stopVideo();
    };
    // 只在采用范围内循环：到出点（或文件结尾）就回到入点
    const loop = () => { if (range) { video.currentTime = range.from; video.play().catch(() => {}); } };
    video.addEventListener('timeupdate', () => { if (range && video.currentTime >= range.to) loop(); });
    video.addEventListener('ended', loop);
    video.addEventListener('loadedmetadata', () => { if (anchor) place(peek, anchor, 'start'); });
    img.addEventListener('error', () => { img.hidden = true; });
    img.addEventListener('load', () => { if (anchor) place(peek, anchor, 'start'); });
    const target = event => event.target.closest && event.target.closest('[data-asset]');
    const hoverTarget = event => event.target.closest && event.target.closest('[data-asset], [data-peek-shot], [data-peek-video]');
    document.addEventListener('pointerover', event => { const node = hoverTarget(event); if (node) show(node); else hide(); });
    document.addEventListener('focusin', event => { const node = target(event); if (node) show(node); else hide(); });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') hide();
      const node = target(event);
      if (node && node.getAttribute('role') === 'button' && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault(); copyAsset(assetFor(node), node);
      }
    });
    document.addEventListener('scroll', hide, true);
    document.addEventListener('click', event => {
      const node = target(event);
      if (node) copyAsset(assetFor(node), node);
    });
  }

  /* ---------- 未保存修改对话框：保存 / 放弃修改 / 取消 ---------- */
  function askUnsaved(message) {
    const dialog = $('confirm-dialog');
    $('confirm-text').textContent = message;
    dialog.returnValue = 'cancel';
    dialog.showModal();
    dialog.querySelector('[data-choice="save"]').focus();
    return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue || 'cancel'), {once: true}));
  }

  function setupDialog() {
    for (const dialog of [$('confirm-dialog'), $('external-dialog')]) dialog.addEventListener('click', event => {
      const choice = event.target.closest('[data-choice]');
      if (choice) dialog.close(choice.dataset.choice);
    });
  }

  /* ---------- 编辑与保存 ---------- */
  function canEdit() { return state.doc.status !== 'final' && state.doc.confirmation !== 'confirmed'; }

  function status(message) {
    const node = $('status');
    node.textContent = message;
    pulse(node, 'refresh');
  }

  function changed() {
    state.dirty = true;
    refreshBeats(followDurations());
    renderChrome(); renderTimeline();
  }

  function setEditing(on) {
    let pendingNormalize = false;
    closeBeat(false);
    if (!on) {
      // 回到只读：显示上次保存的版本，时长照样按 0.1 秒规整显示，但不算未保存
      state.working = clone(state.doc);
      if (canEdit()) normalizeDurations(state.working);
      state.dirty = false;
      if (!shotById(state.selectedId)) state.selectedId = shots().length ? shots()[0].id : null;
      seedDurations();
    } else {
      // 解锁：只读时规整过的时长与文件不同，算作待保存的修改
      pendingNormalize = JSON.stringify(state.working) !== JSON.stringify(state.doc);
      state.dirty = pendingNormalize;
    }
    state.editing = on;
    editEffect(on);
    status(!on ? '已退出编辑，内容保持为上次保存的版本。'
      : pendingNormalize ? '已解锁编辑：按 0.1 秒规整的时长尚未写入文件，按 Ctrl+S 保存后生效。'
      : '已解锁编辑：可直接在表格中修改。按 Ctrl+S 或点击保存。');
    render();
  }

  async function toggleEditing() {
    if (!state.editing) { setEditing(true); return; }
    if (!state.dirty) { setEditing(false); return; }
    const choice = await askUnsaved('退出编辑前，要保存这些修改吗？');
    if (choice === 'save') save(() => setEditing(false));
    else if (choice === 'discard') setEditing(false);
  }

  /* 全页面提示：进入时闪光自左上划向右下、星芒依次闪烁，取景器角标留在四角；退出时沿原路返回。 */
  function editEffect(on) {
    const fx = $('edit-fx');
    fx.classList.toggle('on', on);
    fx.classList.remove('sweep-in', 'sweep-out');
    void fx.offsetWidth; // 重新触发动画，快速连续切换时从头播放当前方向
    fx.classList.add(on ? 'sweep-in' : 'sweep-out');
  }

  /* 与 v2 契约一致的前端预检；完整校验仍由宿主执行。 */
  function problem() {
    const numbers = new Set();
    for (const shot of shots()) {
      const number = shot.number.trim().toLocaleLowerCase();
      if (!number) return '每个镜头都需要镜号。';
      if (numbers.has(number)) return '镜号 ' + shot.number.trim() + ' 重复；修改仍未保存。';
      numbers.add(number);
      if (filled(shot.duration_seconds) && (seconds(shot) === null || !DURATION_PATTERN.test(shot.duration_seconds.trim())))
        return shot.number + ' 的时长应是大于零的秒数，最多精确到 0.1 秒；未决定时留空。';
    }
    if (!validAspect(state.working.layout_aspect || '')) return '预览画幅应写成“宽:高”，例如 16:9；未决定时留空。';
    return null;
  }

  let pendingSave = null, afterSave = null, uncertain = false;

  function save(then) {
    if (state.saving || !state.editing || !state.dirty) return;
    if (uncertain) { status(SAVE_MESSAGES.write_unconfirmed); return; }
    const issue = problem();
    if (issue) { status(issue); return; }
    if (!host) { status('预览模式：没有连接保存程序，修改没有写入任何文件。'); return; }
    pendingSave = clone(state.working);
    pendingSave.revision = state.doc.revision + 1;
    afterSave = then || null;
    state.saving = true;
    renderChrome();
    status('正在保存当前文件…');
    try { host.postMessage({type: 'save', model: pendingSave}); }
    catch (_) { finishSave('unreachable'); }
  }

  function finishSave(outcome) {
    const next = pendingSave, then = afterSave;
    pendingSave = null; afterSave = null; state.saving = false;
    if (outcome === 'saved') {
      state.doc = next;
      // 保存期间继续输入的内容保留在 working 中，仍算未保存
      state.working.revision = next.revision;
      state.dirty = JSON.stringify(state.working) !== JSON.stringify(next);
      renderChrome();
      status(state.dirty ? '已保存；之后的新修改尚未保存。' : '已保存。');
      if (then && !state.dirty) then();
      return;
    }
    if (outcome === 'write_unconfirmed') uncertain = true;
    renderChrome();
    status(outcome === 'unreachable' ? '无法连接保存程序，修改仍未保存。' : SAVE_MESSAGES[outcome] || SAVE_MESSAGES.invalid);
  }

  /* Agent 等外部程序改了文件：没有未保存的修改就原地换成新内容；有的话让用户二选一。
     对话框开着时又改了，选完后用最新的那一版。 */
  let externalModel = null;
  async function onExternal(data) {
    if (data.status !== 'changed') {
      status('文件已在外部修改，但内容无法读取或不符合格式；界面仍是原内容，保存会被拦截。');
      return;
    }
    const waiting = externalModel !== null;
    externalModel = data.model;
    if (waiting) return;
    const keep = state.dirty && await askExternal() === 'keep';
    const model = externalModel;
    externalModel = null;
    if (keep) keepMine(model); else loadExternal(model);
  }

  function askExternal() {
    const dialog = $('external-dialog');
    dialog.returnValue = 'keep';   // Esc 关闭时保留用户的修改，不丢东西
    dialog.showModal();
    dialog.querySelector('[data-choice="load"]').focus();
    return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue || 'keep'), {once: true}));
  }

  /* 换成外部版本：选中的镜头还在就保持，撤销记录清空（撤回去会丢掉外部修改）。 */
  function loadExternal(model) {
    const before = state.doc, rows = [...$('shot-rows').children], clips = [...$('track').children];
    state.doc = model;
    state.working = clone(model);
    if (canEdit()) normalizeDurations(state.working);
    state.dirty = state.editing && JSON.stringify(state.working) !== JSON.stringify(model);
    history.past.length = 0; history.future.length = 0;
    seedDurations();
    state.timelineSel = null;
    if (!shotById(state.selectedId)) state.selectedId = shots().length ? shots()[0].id : null;
    leaveGone = true;
    if (state.editing && !canEdit()) { setEditing(false); }
    else render();
    leaveGone = false;
    showExternal(before, model, rows, clips);
    status('文件已在外部更新（修订 ' + model.revision + '），已载入。');
  }

  /* 外部修改载入后对比前后两版：新建或改动的镜头（表格行、时间线片段、详情栏里改了的块）、泳道条目、标记与标题闪一下强调色；
     删除的镜头在原位置留一个警告色残影后淡出（泳道条目由 syncKeyed 处理）。只标当前画面里的，不留状态。 */
  const GONE_MS = 1400;
  function showExternal(before, after, rows, clips) {
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    const oldShots = new Map(before.shots.map(shot => [shot.id, shot]));
    const fresh = new Map();   // 镜头 id → 改了的字段（新建为 null）
    for (const shot of after.shots) {
      const old = oldShots.get(shot.id);
      if (!old) fresh.set(shot.id, null);
      else {
        const fields = Object.keys(shot).filter(key => !same(shot[key], old[key]));
        if (fields.length) fresh.set(shot.id, new Set(fields));
      }
    }
    // 时间线：主轨片段的变化算到镜头的“视频”上；其他条目按 id 比对
    const items = t => !t ? new Map() : new Map([...t.video, ...t.audio].flatMap(track => track.clips)
      .concat(t.subtitles.flatMap(track => track.cues), t.markers).map(item => [item.id, item]));
    const oldItems = items(before.timeline);
    const mainShot = new Map(after.timeline ? after.timeline.video[0].clips.map(clip => [clip.id, clip.shot_id]) : []);
    const freshItems = [];
    for (const [id, item] of items(after.timeline)) {
      if (same(item, oldItems.get(id))) continue;
      const shotId = mainShot.get(id);
      if (shotId) fresh.set(shotId, fresh.get(shotId) === null ? null : new Set([...(fresh.get(shotId) || []), 'video']));
      else freshItems.push(id);
    }
    const flash = node => { if (node) pulse(node, 'is-fresh'); };
    for (const [id, fields] of fresh) {
      flash($('shot-rows').querySelector('tr[data-id="' + CSS.escape(id) + '"]'));
      flash($('track').querySelector('.clip[data-id="' + CSS.escape(id) + '"]'));
      if (id !== state.selectedId || state.timelineSel) continue;
      const blocks = {description: '#f-description', prompt: '#f-prompt', video_prompt: '#f-video-prompt', video: '.block-video'};
      for (const [field, selector] of Object.entries(blocks)) {
        if (fields === null || fields.has(field)) flash($('detail').querySelector(selector) && $('detail').querySelector(selector).closest('.block'));
      }
    }
    for (const id of freshItems) {
      flash(document.querySelector('#lanes :is(.media-clip, .cue)[data-id="' + CSS.escape(id) + '"]'));
      flash($('marker-band').querySelector('[data-marker="' + CSS.escape(id) + '"]'));
    }
    if (before.title !== after.title) flash($('doc-title'));
    // 删除的镜头：表格行插回原位置、片段留在原坐标，警告色淡出后移除
    const gone = id => !after.shots.some(shot => shot.id === id);
    const ghost = (node, place) => {
      node.classList.remove('is-fresh');
      node.classList.add('is-gone');
      node.inert = true;
      place(node);
      setTimeout(() => collapse(node), reducedMotion() ? 0 : GONE_MS);
    };
    // 残影淡出后移除；表格里下面的行从原位置滑上来，不直接跳
    const collapse = node => {
      const below = node.tagName === 'TR' ? [...node.parentNode.children].slice([...node.parentNode.children].indexOf(node) + 1) : [];
      const height = node.offsetHeight;
      node.remove();
      if (!reducedMotion()) for (const row of below) row.animate([{translate: '0 ' + height + 'px'}, {translate: '0 0'}], {duration: 240, easing: 'ease-out'});
    };
    rows.forEach((row, index) => {
      if (!row.dataset.id || !gone(row.dataset.id)) return;
      const prev = rows.slice(0, index).reverse().find(node => !gone(node.dataset.id));
      const anchor = prev && $('shot-rows').querySelector('tr[data-id="' + CSS.escape(prev.dataset.id) + '"]');
      ghost(row, node => anchor ? anchor.after(node) : $('shot-rows').prepend(node));
    });
    for (const node of clips) if (node.dataset.id && gone(node.dataset.id)) ghost(node, item => $('track').append(item));
  }

  /* 保留用户的修改：以外部版本为保存基准，下次保存覆盖外部修改。文档身份跟随外部版本，否则保存会被拦截。 */
  function keepMine(model) {
    state.doc = model;
    for (const key of ['document_id', 'version', 'status', 'confirmation']) state.working[key] = model[key];
    if (model.timeline && !state.working.timeline) state.working.timeline = clone(model.timeline);
    state.working.schema = state.working.timeline ? SCHEMA_V3 : model.schema;
    renderChrome();
    status('已保留你的修改；保存时会覆盖外部的修改。');
  }

  /* 宿主关闭窗口前读取状态；有未保存修改时由页面询问，再通知宿主允许关闭。 */
  window.__kplCloseState = () => ({dirty: state.dirty, saving: state.saving});
  window.__kplRequestClose = () => {
    askUnsaved('关闭窗口前，要保存这些修改吗？').then(choice => {
      if (choice === 'save') save(() => host.postMessage({type: 'closeApproved'}));
      else if (choice === 'discard') host.postMessage({type: 'closeApproved'});
    });
    return true;
  };

  /* ---------- 显示设置与布局偏好：原生环境由宿主写入本地应用数据，预览环境存在浏览器本地 ---------- */
  function readPrefs() {
    const node = $('kpl-prefs');
    try { return Object.assign({}, DEFAULT_PREFS, JSON.parse(node ? node.textContent : localStorage.getItem('kpl-prefs') || '{}')); }
    catch (_) { return Object.assign({}, DEFAULT_PREFS); }
  }
  const prefs = readPrefs();

  function savePrefs() {
    if (host) { host.postMessage({type: 'prefs', prefs}); return; }
    try { localStorage.setItem('kpl-prefs', JSON.stringify(prefs)); } catch (_) { /* 预览环境写不了就不记住 */ }
  }

  function applyPrefs(prefs) {
    document.documentElement.dataset.mode = prefs.mode;
    document.documentElement.dataset.accent = prefs.accent;
    for (const input of document.querySelectorAll('#display-panel input')) input.checked = prefs[input.name] === input.value;
  }

  function setupDisplay() {
    const trigger = $('display-trigger'), panel = $('display-panel');
    applyPrefs(prefs);
    const open = value => {
      panel.hidden = !value;
      trigger.setAttribute('aria-expanded', String(value));
      if (value) panel.querySelector('input:checked').focus();
    };
    trigger.addEventListener('click', () => open(panel.hidden));
    panel.addEventListener('change', event => {
      prefs[event.target.name] = event.target.value;
      applyPrefs(prefs);
      savePrefs();
    });
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { open(false); trigger.focus(); }
    });
    document.addEventListener('pointerdown', event => {
      if (!panel.hidden && !panel.contains(event.target) && !trigger.contains(event.target)) open(false);
    });
    panel.addEventListener('focusout', event => {
      if (event.relatedTarget && !panel.contains(event.relatedTarget) && event.relatedTarget !== trigger) open(false);
    });
  }

  /* ---------- 关于与更新提示 ---------- */
  function showVersion(version) { $('about-version').textContent = 'v' + version; }

  function showUpdate(version) {
    const badge = $('update-badge');
    badge.hidden = false;
    badge.querySelector('span').textContent = '新版本 v' + version;
    status('发现新版本 v' + version + '，点击右上角的提示前往发布页。');
  }

  function setupAbout() {
    const trigger = $('about-trigger'), panel = $('about-panel');
    const open = value => {
      panel.hidden = !value;
      trigger.setAttribute('aria-expanded', String(value));
      if (value) panel.focus();
    };
    trigger.addEventListener('click', () => open(panel.hidden));
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { open(false); trigger.focus(); }
    });
    document.addEventListener('pointerdown', event => {
      if (!panel.hidden && !panel.contains(event.target) && !trigger.contains(event.target)) open(false);
    });
    // 网址由宿主决定：页面只请求“打开发布页”
    $('update-badge').addEventListener('click', () => { if (host) host.postMessage({type: 'openUpdate'}); });
  }

  /* ---------- 缩略图：宿主在后台生成并汇报进度，就绪的才载入 ---------- */
  /* 只原地替换占位与缺失的胶片条，不重绘整个界面（避免丢失焦点与编辑状态）。 */
  function applyThumbs(paths) {
    if (!paths.size) return;
    for (const node of document.querySelectorAll('[data-thumb-shot]')) {
      const shot = shotById(node.dataset.thumbShot);
      if (shot && paths.has(shot.thumbnail)) node.replaceWith(thumb(shot));
    }
    for (const node of document.querySelectorAll('[data-thumb-asset]')) {
      const asset = state.working.assets.find(item => item.id === node.dataset.thumbAsset);
      const url = asset && paths.has(asset.path) ? thumbUrl(asset.path) : null;
      if (url) node.replaceWith(el('img', {class: 'asset-thumb', src: url, alt: ''}));
    }
    for (const node of document.querySelectorAll('.clip[data-id]')) {
      const shot = shotById(node.dataset.id);
      const url = shot && paths.has(shot.thumbnail) ? thumbUrl(shot.thumbnail) : null;
      if (!url || node.querySelector('.clip-strip')) continue;
      const strip = el('span', {class: 'clip-strip'});
      strip.style.backgroundImage = 'url("' + url + '")';
      node.prepend(strip);
    }
    const current = preview.current ? shotById(preview.current) : null;
    if (current && paths.has(current.thumbnail)) drawPreview();
  }

  function onThumbs(data) {
    const ready = data.ready || {};
    for (const [path, name] of Object.entries(ready)) thumbs.map.set(path, name);
    applyThumbs(new Set(Object.keys(ready)));
    const note = $('thumb-progress');
    if (!data.finished) {
      thumbs.building = true;
      note.hidden = false;
      note.textContent = '正在生成缩略图 ' + data.done + '/' + data.total;
      return;
    }
    note.hidden = true;
    if (!thumbs.building) return;   // 缓存本来就齐全时不打扰
    thumbs.building = false;
    const failed = data.total - thumbs.map.size;
    status(failed > 0 ? '缩略图已生成，' + failed + ' 张无法生成，保持占位。' : '缩略图已全部生成。');
  }

  /* ---------- 事件 ---------- */
  function setupEvents() {
    // 只读时不可用的控件（aria-disabled）按下鼠标不拿焦点，免得出现焦点指示条、看起来像点中了
    document.addEventListener('mousedown', event => {
      if (event.target.closest && event.target.closest('[aria-disabled="true"]')) event.preventDefault();
    });
    $('tab-table').addEventListener('click', () => setView('table'));
    $('tab-timeline').addEventListener('click', () => setView('timeline'));
    document.querySelector('[role="tablist"]').addEventListener('keydown', event => {
      const order = ['table', 'timeline'], index = order.indexOf(state.view);
      const next = {ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: order.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      setView(order[(next + order.length) % order.length], true);
    });
    $('shot-rows').addEventListener('keydown', event => {
      if (state.editing || !event.target.classList.contains('cell-number')) return;
      const delta = {ArrowDown: 1, ArrowUp: -1}[event.key];
      if (delta) { event.preventDefault(); moveSelection(delta); }
    });
    // 编辑时焦点进入某一行即选中该行
    $('shot-rows').addEventListener('focusin', event => {
      const row = event.target.closest('tr[data-id]');
      if (row) select(row.dataset.id);
    });
    $('view-timeline').addEventListener('keydown', event => {
      if (event.target.closest('.marker') && state.editing) {
        if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); deleteItem(); }
        return;
      }
      const lane = event.target.closest('.media-clip, .cue');
      if (lane && state.editing) {
        if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); deleteItem(); return; }
        if (event.key.toLowerCase() === 'k' && event.ctrlKey) { event.preventDefault(); splitItem(); return; }
        const delta = {ArrowRight: 1, ArrowLeft: -1}[event.key];
        if (delta && !event.ctrlKey) { event.preventDefault(); nudgeLane(delta, event.shiftKey); return; }
        return;
      }
      if (!event.target.classList.contains('clip')) return;
      if (nudgeClip(event)) { event.preventDefault(); return; }
      const delta = {ArrowRight: 1, ArrowLeft: -1}[event.key];
      if (delta) { event.preventDefault(); moveSelection(delta); }
    });
    $('edit-toggle').addEventListener('click', toggleEditing);
    $('save').addEventListener('click', () => save());
    // Ctrl+Z 撤销、Ctrl+Shift+Z 或 Ctrl+Y 重做；只影响编辑中的文档，只读时不起作用
    document.addEventListener('keydown', event => {
      if (!state.editing || !(event.ctrlKey && !event.altKey && !event.metaKey)) return;
      if (event.target.closest && event.target.closest('input, textarea, [contenteditable]')) return;
      const key = event.key.toLowerCase();
      if (key === 's') { event.preventDefault(); save(); }
      else if (key === 'z') { event.preventDefault(); timeTravel(!event.shiftKey); }
      else if (key === 'y') { event.preventDefault(); timeTravel(false); }
    });
    if (host) host.addEventListener('message', event => {
      const data = event.data || {};
      if (data.type === 'saveResult' && pendingSave) finishSave(data.status);
      else if (data.type === 'copyTextResult') status(data.status === 'copied' ? '已复制提示词，引用已换成 @资产名。' : '复制失败：剪贴板被占用，请重试。');
      else if (data.type === 'copyResult') status(data.status === 'copied' ? '已复制图片。' : '复制失败：图片无法读取或剪贴板被占用。');
      else if (data.type === 'info') showVersion(data.version);
      else if (data.type === 'update') showUpdate(data.version);
      else if (data.type === 'thumbs') onThumbs(data);
      else if (data.type === 'mediaPicked') onMediaPicked(data);
      else if (data.type === 'mediaUrls') onMediaUrls(data.urls);
      else if (data.type === 'subtitlePicked') onSubtitlePicked(data);
      else if (data.type === 'external') onExternal(data);
    });
  }

  setupDisplay();
  setupAbout();
  setupMenu();
  setupPlainCopy();
  setupBeatPop();
  setupPeek();
  setupDialog();
  setupTimelineDrag();
  setupLaneDrag();
  setupMarkers();
  setupEditToggles();
  setupContextMenu();
  setupTimelineView();
  setupMonitor();
  setupAspect();
  setupHelp();
  setupEvents();
  // 旧文件的时长按 0.1 秒规整后显示。只读时不算未保存（关窗口不需要保存，也不会被保存卡住）；解锁后差异才算未保存，保存时写回
  const normalized = canEdit() ? normalizeDurations(state.working) : 0;
  seedDurations();
  render();
  status(!canEdit() ? '这是已确认或定稿的版本，只能查看。'
    : normalized ? '有 ' + normalized + ' 个镜头的时长已按 0.1 秒规整显示（按累计切点取整，总长不变）；文件尚未改动，解锁编辑并保存后才写回。'
    : '只读查看中。需要修改时点击“解锁编辑”。');
  if (host) { host.postMessage({type: 'ready'}); media.live = true; resolveMedia(); }
})();
