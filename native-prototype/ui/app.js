/* KPL 编辑器界面：表格与 Layout 时间线共享同一份文档、选中项与未保存状态。 */
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
     没有标记的文字不染色；分段内可以有 @{…} 引用。 */
  const SEGMENT_TAGS = {镜头: 'camera', 风格: 'style', 场景: 'scene', 动态: 'action'};
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

  const segNode = (tag, parts, interactive) => el('span', {class: 'seg seg-' + SEGMENT_TAGS[tag], 'data-tag': tag, title: tag},
    parts.map(part => partNode(part, interactive)));
  const endBreak = () => el('br', {'data-end': ''});

  function richNodes(text, interactive) {
    const nodes = segments(text).map(item => item.parts ? segNode(item.tag, item.parts, interactive) : partNode(item, interactive));
    if ((text || '').endsWith('\n')) nodes.push(endBreak());
    return nodes;
  }

  /* 把编辑区还原成带分段标记的文字；内容被删空的分段直接去掉。 */
  const childText = node => [...node.childNodes].map(nodeText).join('');
  function nodeText(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.data;
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
        if (inner) nodes.push(segNode(child.dataset.tag, inlineParts(inner), false));
      }
      flush();
      if (text.endsWith('\n')) nodes.push(endBreak());
    }
    box.replaceChildren(...nodes);
    if (at !== null) setCaret(box, at);
  }

  /* 光标与选区按可见文字的偏移记录，重绘后再按偏移放回。 */
  function textOffset(root, node, at) {
    const range = document.createRange();
    range.selectNodeContents(root);
    range.setEnd(node, at);
    return range.toString().length;
  }

  function pointAt(root, offset) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (offset <= node.data.length) return [node, offset];
      offset -= node.data.length;
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
        if (child.nodeType !== Node.TEXT_NODE && child.nodeName !== 'BR') { walk(child, child.dataset.tag || inherited); continue; }
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
    const editing = state.editing;
    const box = el('div', {id, class: 'rich', role: 'textbox', 'aria-multiline': 'true', 'aria-labelledby': id + '-title',
      'aria-readonly': editing ? null : 'true', contenteditable: editing ? 'plaintext-only' : null, spellcheck: 'false'},
      richNodes(shot[key], !editing));
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
            box.replaceChildren(...richNodes(result.text, false));
            shot[key] = result.text;
            recount();
            box.focus(); selectSpan(box, result.span);
            onChange();
          }
        }))
      });
    });
    return el('section', {class: 'block'}, [el('h3', {class: 'block-title', id: id + '-title'}, [el('span', {text: title}), count]), box]);
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
        markOf(shot.mark) ? markIcon(shot) : null
      ]),
      state.editing && onTrack ? el('span', {class: 'clip-handle', title: '拖动调整时长'}) : null
    ]);
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
  const layout = {pps: 0};  // 最近一次排布用的比例尺，播放头、跳转与拖动都按它换算

  function fitPps() {
    const total = plan().total;
    return total > 0 ? $('timeline-scroll').clientWidth / total : 0;
  }
  const currentPps = () => zoom.pps || fitPps();

  function renderTimeline() {
    const {placed, pending, total} = plan();
    $('timeline').classList.toggle('is-editing', state.editing);
    // 解锁后即使还没有排布的镜头也保留轨道，作为待定镜头的落点
    $('track').hidden = total === 0 && !state.editing;
    $('track').replaceChildren(...placed.map(item => clip(item.shot, true)));
    $('pending-lane').hidden = pending.length === 0;
    $('pending-list').replaceChildren(...pending.map(shot => clip(shot, false)));
    layoutTimeline(currentPps());
    renderMonitor();
  }

  /* 按比例尺排布标尺、片段与播放头。拖时长时沿用开始拖动时的比例尺，避免整条时间线跟着缩放。 */
  function layoutTimeline(pps) {
    const {placed, total} = plan();
    layout.pps = pps;
    $('timeline').style.inlineSize = total * pps + 'px';
    const [step, parts] = TICK_STEPS.find(([candidate]) => candidate * pps >= TICK_GAP) || TICK_STEPS[TICK_STEPS.length - 1];
    $('ruler').style.setProperty('--minor', step * pps / parts + 'px');
    const marks = total > 0 ? ticks(total, step) : [];
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
      if (dragging && item.shot === dragging.shot) node.querySelector('.clip-dur').textContent = round(item.length) + '秒';
    }
    placePlayhead();
    updateNav();
  }

  /* 以屏幕横坐标 clientX 处的时间点为锚缩放：缩放后该时间点仍停在原来的位置。 */
  function setZoom(pps, clientX) {
    const fit = fitPps();
    if (!fit || !layout.pps) return;
    const scroller = $('timeline-scroll'), timeline = $('timeline');
    const time = (clientX - timeline.getBoundingClientRect().left) / layout.pps;
    const next = Math.min(Math.max(pps, fit), Math.max(MAX_PPS, fit));
    zoom.pps = next <= fit * 1.001 ? null : next;
    layoutTimeline(currentPps());
    scroller.scrollLeft += timeline.getBoundingClientRect().left + time * layout.pps - clientX;
  }

  /* 按钮与快捷键缩放以播放头为锚；播放头不在可见区时以可见区中央为锚。 */
  function zoomAnchor() {
    const rect = $('timeline-scroll').getBoundingClientRect();
    const x = $('timeline').getBoundingClientRect().left + Math.min(preview.time, plan().total) * layout.pps;
    return x >= rect.left && x <= rect.right ? x : rect.left + rect.width / 2;
  }
  const zoomBy = factor => setZoom(currentPps() * factor, zoomAnchor());
  const zoomFit = () => setZoom(0, zoomAnchor());

  /* 导航条代替原生滚动条：滑块的位置与宽度对应可见区在整条时间线中的位置与比例。 */
  function updateNav() {
    const scroller = $('timeline-scroll'), width = scroller.scrollWidth || 1, thumb = $('timeline-nav-thumb');
    thumb.style.left = scroller.scrollLeft / width * 100 + '%';
    thumb.style.width = Math.min(1, scroller.clientWidth / width) * 100 + '%';
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
  }
  function setPanelHeight(height) {
    prefs.panelH = Math.round(Math.max(PANEL_MIN, Math.min(height, $('view-timeline').clientHeight - MONITOR_MIN)));
    applyLayout();
  }
  function setAsideWidth(width) {
    prefs.asideW = Math.round(Math.max(ASIDE_MIN, Math.min(width, document.querySelector('.workspace').clientWidth - VIEW_MIN)));
    applyLayout();
  }

  /* 分隔条按住拖动或用方向键调整；指针朝 axis 正方向移动时尺寸减小（面板在下方、详情栏在右侧）。 */
  function setupSplitter(splitter, current, resize, axis, keys) {
    splitter.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const start = event[axis], from = current();
      splitter.classList.add('is-dragging');
      followPointer(splitter, event, move => resize(from - (move[axis] - start)),
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
    // 滚轮横向滚动；Ctrl 或 Alt+滚轮（含触控板捏合）以指针位置为锚缩放
    scroller.addEventListener('wheel', event => {
      event.preventDefault();
      if (event.ctrlKey || event.altKey) setZoom(currentPps() * Math.exp(-event.deltaY * 0.002), event.clientX);
      else scroller.scrollLeft += event.deltaX + event.deltaY;
    }, {passive: false});
    scroller.addEventListener('scroll', updateNav);
    new ResizeObserver(() => { if (!dragging) layoutTimeline(currentPps()); }).observe(scroller);
    $('zoom-in').addEventListener('click', () => zoomBy(1.5));
    $('zoom-out').addEventListener('click', () => zoomBy(1 / 1.5));
    $('zoom-fit').addEventListener('click', zoomFit);

    nav.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      const rect = nav.getBoundingClientRect(), ratio = scroller.scrollWidth / rect.width;
      if (event.target === nav) scroller.scrollLeft = (event.clientX - rect.left) * ratio - scroller.clientWidth / 2;
      const x = event.clientX, from = scroller.scrollLeft;
      nav.classList.add('is-dragging');
      followPointer(nav, event, move => { scroller.scrollLeft = from + (move.clientX - x) * ratio; },
        () => nav.classList.remove('is-dragging'));
    });

    applyLayout();
    setupSplitter(splitter, () => panel.offsetHeight, setPanelHeight, 'clientY', {ArrowUp: 16, ArrowDown: -16});
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
      if (!state.editing || !node || event.button !== 0) return;
      const shot = shotById(node.dataset.id);
      dragging = {node, shot, x: event.clientX, y: event.clientY, moved: false, target: null,
        resize: event.target.classList.contains('clip-handle'), pending: node.parentElement.id === 'pending-list',
        length: seconds(shot), original: shot.duration_seconds, pps: layout.pps};
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
        const length = snap(dragging.length + dx / dragging.pps);
        if (length !== seconds(dragging.shot)) { dragging.shot.duration_seconds = String(length); layoutTimeline(dragging.pps); }
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
      const {node, shot, moved, resize, pending, target, length, original} = dragging;
      dragging = null;
      $('drop-mark').hidden = true;
      $('timeline').classList.remove('is-dragging');
      node.classList.remove('is-dragging');
      node.style.translate = '';
      if (!moved) return;
      suppressClick = true;
      setTimeout(() => { suppressClick = false; });
      if (resize) {
        if (commit && seconds(shot) !== length) { commitTimeline(shot.id, false); return; }
        shot.duration_seconds = original;
        renderTimeline();
        return;
      }
      if (!commit || !target) return;
      if (pending) shot.duration_seconds = String(PLACE_SECONDS);
      const reordered = moveShot(shot.id, target.beforeId);
      if (pending || reordered) commitTimeline(shot.id, reordered);
    };
    view.addEventListener('pointerup', () => finish(true));
    view.addEventListener('pointercancel', () => finish(false));
    view.addEventListener('keydown', event => {
      if (event.key === 'Escape' && dragging) finish(false);
    });
  }

  /* 键盘替代拖动：Alt+←/→ 与相邻片段交换顺序，Shift+←/→ 时长 ±0.1 秒。 */
  function nudgeClip(event) {
    const node = event.target;
    if (!state.editing || node.parentElement.id !== 'track') return false;
    const step = {ArrowLeft: -1, ArrowRight: 1}[event.key];
    if (!step || event.altKey === event.shiftKey) return false;
    const shot = shotById(node.dataset.id), placed = plan().placed.map(item => item.shot.id);
    const index = placed.indexOf(shot.id);
    if (event.shiftKey) {
      shot.duration_seconds = String(snap(seconds(shot) + step * 0.1));
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

  /* 监视器按画幅取可用区域内最大的等比尺寸，由外层居中。 */
  function sizeMonitor() {
    const stage = $('monitor-stage'), frame = $('monitor-frame'), ratio = aspectRatio();
    const width = Math.floor(Math.min(stage.clientWidth, stage.clientHeight * ratio));
    frame.style.inlineSize = width + 'px';
    frame.style.blockSize = Math.floor(width / ratio) + 'px';
  }

  function placePlayhead() {
    const total = plan().total;
    $('playhead').hidden = total <= 0;
    $('playhead').style.left = Math.min(preview.time, total) * layout.pps + 'px';
  }

  /* 播放时播放头走出可见区就翻页，与剪辑软件一致。 */
  function followPlayhead() {
    const scroller = $('timeline-scroll'), x = Math.min(preview.time, plan().total) * layout.pps;
    if (x < scroller.scrollLeft || x > scroller.scrollLeft + scroller.clientWidth) scroller.scrollLeft = x - 16;
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

  function drawPreview() {
    const {placed, total} = plan();
    const time = Math.min(preview.time, total);
    const item = placed.find(entry => time < entry.start + entry.length) || placed[placed.length - 1] || null;
    $('timecode').textContent = clock(time);
    $('timecode-total').textContent = clock(total);
    placePlayhead();
    if (preview.playing) followPlayhead();
    $('monitor-shot').textContent = item ? item.shot.number : '';
    $('monitor-text').textContent = item ? item.shot.content || UNKNOWN : '没有可预览的镜头：先确认时长。';
    const id = item ? item.shot.id : null;
    if (preview.current !== id) {
      preview.current = id;
      for (const node of $('track').children) node.classList.toggle('is-current', node.dataset.id === id);
    }

    const frame = $('monitor-frame'), canvas = $('monitor-canvas'), scale = window.devicePixelRatio || 1;
    const width = Math.round(frame.clientWidth * scale), height = Math.round(frame.clientHeight * scale);
    if (!width || !height) return;
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    const ctx = canvas.getContext('2d'), style = getComputedStyle(frame);
    ctx.fillStyle = style.backgroundColor;
    ctx.fillRect(0, 0, width, height);
    if (!item) return;
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
    $('play-toggle').replaceChildren(icon(preview.playing ? 'pause' : 'play'), el('span', {text: preview.playing ? '暂停' : '播放'}));
    $('play-toggle').disabled = plan().total <= 0;
  }

  function setPlaying(on) {
    const {total} = plan();
    if (on && total <= 0) return;
    if (on && preview.time >= total) preview.time = 0;
    preview.playing = on;
    playButton();
    if (on) { preview.last = performance.now(); requestAnimationFrame(tick); }
  }

  function tick(now) {
    if (!preview.playing) return;
    const {total} = plan();
    preview.time += (now - preview.last) / 1000;
    preview.last = now;
    if (preview.time >= total) { preview.time = total; setPlaying(false); }
    drawPreview();
    if (preview.playing) requestAnimationFrame(tick);
  }

  function seek(time) {
    preview.time = Math.min(Math.max(0, time), plan().total);
    drawPreview();
  }

  const seekAt = clientX => { if (layout.pps) seek((clientX - $('timeline').getBoundingClientRect().left) / layout.pps); };

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
      const zoomKey = {'=': () => zoomBy(1.5), '+': () => zoomBy(1.5), '-': () => zoomBy(1 / 1.5), '\\': zoomFit}[event.key];
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
      textBlock('f-prompt', '图片生成提示词', shot, 'prompt', refresh),
      textBlock('f-video-prompt', '视频提示词', shot, 'video_prompt', refresh)
    ];
  }

  /* 切换镜头时旧内容作为残影淡出、新内容同时淡入；不延迟新内容的出现。 */
  function renderDetail(crossfade) {
    const detail = $('detail');
    const old = [...detail.children].filter(node => !node.classList.contains('detail-ghost'));
    if (crossfade && old.length && !reducedMotion()) {
      detail.scrollTop = 0;
      const ghost = el('div', {class: 'detail-ghost', 'aria-hidden': 'true', inert: true}, old);
      ghost.addEventListener('animationend', () => ghost.remove(), {once: true});
      detail.querySelectorAll('.detail-ghost').forEach(node => node.remove());
      detail.replaceChildren(ghost, ...detailNodes(shotById(state.selectedId)));
      detail.classList.remove('entering'); void detail.offsetWidth; detail.classList.add('entering');
      return;
    }
    detail.replaceChildren(...detailNodes(shotById(state.selectedId)));
  }

  function render() {
    renderChrome(); renderTable(); renderTimeline(); renderDetail();
  }

  /* ---------- 选择与视图 ---------- */
  function selectedNode() {
    const view = state.view === 'table' ? $('view-table') : $('view-timeline');
    return view.querySelector('[data-id="' + CSS.escape(state.selectedId) + '"]');
  }

  /* 只更新已有节点的选中标记，避免重建时丢失焦点。 */
  function markSelection() {
    for (const node of document.querySelectorAll('[data-id]')) {
      const selected = node.dataset.id === state.selectedId;
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
    if (!shotById(id) || id === state.selectedId && !focus) return;
    const changedShot = id !== state.selectedId;
    state.selectedId = id;
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
    if (view === 'timeline') renderMonitor(); else { setPlaying(false); showHelp(false); }
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
    }, [item.icon, el('span', {text: item.label})])));
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
      event.clipboardData.setData('text/plain', selection.toString());
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

  /* ---------- 图片预览：素材引用悬停或聚焦时显示、点击复制；表格缩略图悬停时显示大图 ---------- */
  function setupPeek() {
    const peek = $('asset-peek'), img = $('peek-img');
    let anchor = null;
    const assetFor = node => state.working.assets.find(asset => asset.id === node.dataset.asset);
    const show = node => {
      const shot = node.dataset.peekShot ? shotById(node.dataset.peekShot) : null, asset = shot ? null : assetFor(node);
      if (!shot && !asset || anchor === node) return;
      anchor = node;
      img.hidden = false;
      img.src = thumbUrl(shot ? shot.thumbnail : asset.path) || '';
      $('peek-id').textContent = shot ? shot.number : asset.id;
      $('peek-label').textContent = shot ? shot.content : asset.name;
      peek.classList.toggle('peek-large', Boolean(shot));
      peek.hidden = false;
      node.setAttribute('aria-describedby', 'asset-peek');
      place(peek, node, 'start');
    };
    const hide = () => {
      if (!anchor) return;
      anchor.removeAttribute('aria-describedby');
      anchor = null; peek.hidden = true;
    };
    img.addEventListener('error', () => { img.hidden = true; });
    img.addEventListener('load', () => { if (anchor) place(peek, anchor, 'start'); });
    const target = event => event.target.closest && event.target.closest('[data-asset]');
    const hoverTarget = event => event.target.closest && event.target.closest('[data-asset], [data-peek-shot]');
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
    const dialog = $('confirm-dialog');
    dialog.addEventListener('click', event => {
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
    renderChrome(); renderTimeline();
  }

  function setEditing(on) {
    let pendingNormalize = false;
    if (!on) {
      // 回到只读：显示上次保存的版本，时长照样按 0.1 秒规整显示，但不算未保存
      state.working = clone(state.doc);
      if (canEdit()) normalizeDurations(state.working);
      state.dirty = false;
      if (!shotById(state.selectedId)) state.selectedId = shots().length ? shots()[0].id : null;
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
      if (!event.target.classList.contains('clip')) return;
      if (nudgeClip(event)) { event.preventDefault(); return; }
      const delta = {ArrowRight: 1, ArrowLeft: -1}[event.key];
      if (delta) { event.preventDefault(); moveSelection(delta); }
    });
    $('edit-toggle').addEventListener('click', toggleEditing);
    $('save').addEventListener('click', () => save());
    document.addEventListener('keydown', event => {
      if (event.ctrlKey && !event.altKey && !event.metaKey && event.key.toLowerCase() === 's') {
        event.preventDefault(); save();
      }
    });
    if (host) host.addEventListener('message', event => {
      const data = event.data || {};
      if (data.type === 'saveResult' && pendingSave) finishSave(data.status);
      else if (data.type === 'copyResult') status(data.status === 'copied' ? '已复制图片。' : '复制失败：图片无法读取或剪贴板被占用。');
      else if (data.type === 'info') showVersion(data.version);
      else if (data.type === 'update') showUpdate(data.version);
      else if (data.type === 'thumbs') onThumbs(data);
    });
  }

  setupDisplay();
  setupAbout();
  setupMenu();
  setupPlainCopy();
  setupPeek();
  setupDialog();
  setupTimelineDrag();
  setupTimelineView();
  setupMonitor();
  setupAspect();
  setupHelp();
  setupEvents();
  // 旧文件的时长按 0.1 秒规整后显示。只读时不算未保存（关窗口不需要保存，也不会被保存卡住）；解锁后差异才算未保存，保存时写回
  const normalized = canEdit() ? normalizeDurations(state.working) : 0;
  render();
  status(!canEdit() ? '这是已确认或定稿的版本，只能查看。'
    : normalized ? '有 ' + normalized + ' 个镜头的时长已按 0.1 秒规整显示（按累计切点取整，总长不变）；文件尚未改动，解锁编辑并保存后才写回。'
    : '只读查看中。需要修改时点击“解锁编辑”。');
  if (host) host.postMessage({type: 'ready'});
})();
