/* Recorded runs on the page. The teaser replays requests the harness carried out in HospitalRun:
   the pictures are the frames it captured, and between two frames the pointer, each click and the
   typing are animated on top, so that every action can be seen. Beside it, the console's step list.
   The second part draws what the harness read on single frames. The data is in runs.js, built from
   the run logs. */
(function () {
  'use strict';
  const RUNS = window.CUA_RUNS || [];
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const NS = 'http://www.w3.org/2000/svg';
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const src = (run, n) => `assets/media/runs/${run.key}/${String(n).padStart(2, '0')}.webp`;
  const byKey = key => RUNS.find(r => r.key === key);
  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };

  // Every run is replayed SPEED times as fast as it was recorded, and the picture says so in its
  // corner. These are the timings (ms) of a run before they are stretched to exactly that factor.
  const SPEED = 3;
  const PACE = { move: 320, click: 190, char: 26, key: 420, scroll: 420, tail: 240 };
  const HOLD = { read: 650, plan: [1100, 2100], decide: 950, result: 620, note: 1500 };
  const ANSWER = 4200;   // the answer stays for reading; it is not part of the run's time
  const hold = b => b.kind === 'act' ? PACE.tail
    : b.kind === 'plan' ? Math.max(HOLD.plan[0], Math.min(HOLD.plan[1], 500 + b.say.length * 11))
    : HOLD[b.kind] || 800;
  const busy = b => (b.acts || []).reduce((t, a) => t + (a.t === 'click' ? PACE.move + PACE.click
    : a.t === 'type' ? a.text.length * PACE.char + 120 : a.t === 'key' ? PACE.key : PACE.scroll), 0);

  const ICON = {
    play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor"/></svg>',
    prev: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    next: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.4 3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    cross: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    spin: '<svg class="spin" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2a5.8 5.8 0 1 1-5.8 5.8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    ptr: '<svg viewBox="0 0 20 26" aria-hidden="true"><path d="M2 1.5v19l5-4.7 3.2 7.4 3.4-1.5-3.2-7.3h7z" fill="#0e1626" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    keys: '<svg viewBox="0 0 20 14" aria-hidden="true"><rect x="1" y="1" width="18" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M4.5 4.5h1M8 4.5h1M11.5 4.5h1M15 4.5h.5M4.5 7.5h1M8 7.5h1M11.5 7.5h1M15 7.5h.5M6 10.5h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    mouse: '<svg viewBox="0 0 14 20" aria-hidden="true"><rect x="1" y="1" width="12" height="18" rx="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7 1v7M1 8h12" stroke="currentColor" stroke-width="1.5"/><path d="M1.6 7.2A6 6 0 0 1 7 1v7H1z" fill="currentColor" fill-opacity=".85"/></svg>',
  };
  const WHO = { code: 'Code', planner: 'Planner', decider: 'Decider', check: 'Check', result: 'Answer' };

  const loaded = new Map();
  const load = url => {
    if (!loaded.has(url)) {
      loaded.set(url, new Promise(res => { const im = new Image(); im.onload = im.onerror = () => res(url); im.src = url; }));
    }
    return loaded.get(url);
  };

  // ------------------------------------------------------------------ the teaser
  function Replay(fig) {
    this.fig = fig;
    this.cycle = fig.hasAttribute('data-cycle');
    this.playing = false;
    this.userPaused = false;
    this.timer = 0;
    this.timers = [];
    fig.querySelector('[data-runs]').innerHTML = RUNS.map(r =>
      `<button type="button" data-run="${r.key}" aria-pressed="false">${esc(r.label)}</button>`).join('');
    fig.querySelector('[data-body]').innerHTML =
      '<div class="rp-shot">' +
        '<div class="rp-frame" data-frame>' +
          '<img alt="" data-img width="1181" height="896">' +
          '<div class="rp-typed" data-typed aria-hidden="true"></div>' +
          `<span class="rp-ptr" data-ptr hidden>${ICON.ptr}<i></i></span>` +
          '<div class="rp-hud" data-hud aria-hidden="true"></div>' +
          `<span class="rp-speed" title="Replayed at ${SPEED} times the recorded speed">${SPEED}×<span class="sr"> the recorded speed</span></span>` +
        '</div>' +
        '<p class="rp-say" data-say aria-live="off"><b></b><span></span></p>' +
      '</div>' +
      '<aside class="console" data-console aria-label="The console, as it shows this request">' +
        '<header class="c-head"><span class="c-brand" data-brand></span><div class="c-status"><div class="c-title" data-title></div><div class="c-sub" data-sub></div></div></header>' +
        '<div class="c-progress" data-progress></div>' +
        '<div class="c-thread" data-thread></div>' +
      '</aside>';
    fig.querySelector('[data-ctrl]').innerHTML =
      '<div class="keys">' +
      `<button type="button" class="icon play" data-act="toggle" aria-label="Play">${ICON.play}</button>` +
      `<button type="button" class="icon" data-act="prev" aria-label="Back">${ICON.prev}</button>` +
      `<button type="button" class="icon" data-act="next" aria-label="Forward">${ICON.next}</button>` +
      '</div><ol class="ticks" data-ticks></ol><span class="count" data-count></span>';
    const $ = sel => fig.querySelector(sel);
    this.img = $('[data-img]'); this.typed = $('[data-typed]'); this.ptr = $('[data-ptr]'); this.hud = $('[data-hud]');
    this.say = $('[data-say]'); this.thread = $('[data-thread]'); this.ticks = $('[data-ticks]'); this.count = $('[data-count]');
    this.toggleBtn = $('[data-act="toggle"]');
    this.el = { brand: $('[data-brand]'), title: $('[data-title]'), sub: $('[data-sub]'), progress: $('[data-progress]') };

    fig.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b || !fig.contains(b)) return;
      // Choosing a run starts it from its beginning, whether the replay was playing or not.
      if (b.dataset.run) { this.pause(false); this.load(b.dataset.run); if (!reduced.matches) this.play(); return; }
      if (b.dataset.act === 'toggle') return this.playing ? this.pause(true) : this.play();
      this.pause(true);
      this.say.setAttribute('aria-live', 'polite');
      if (b.dataset.act === 'prev') this.show(Math.max(0, this.i - 1), false);
      else if (b.dataset.act === 'next') this.show(Math.min(this.beats.length - 1, this.i + 1), true);
      else if (b.dataset.beat) this.show(Number(b.dataset.beat), false);
    });
    fig.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      if (e.target.closest('[data-runs]')) return;
      e.preventDefault();
      fig.querySelector(`[data-act="${e.key === 'ArrowLeft' ? 'prev' : 'next'}"]`).click();
    });
    this.load(RUNS[0].key);
  }

  Replay.prototype.later = function (ms, fn) { this.timers.push(setTimeout(fn, ms)); };
  Replay.prototype.clear = function () { this.timers.forEach(clearTimeout); this.timers = []; };

  Replay.prototype.load = function (key) {
    clearTimeout(this.timer);
    this.clear();
    const run = this.run = byKey(key);
    this.beats = run.beats;
    // Stretch this run's timings so that it plays in a SPEED-th of the time it took.
    const natural = this.beats.slice(0, -1).reduce((t, b) => t + busy(b) + hold(b), 0);
    this.k = run.timing.total * 1000 / SPEED / natural;
    this.steps = new Map(run.steps.map(s => [s.id, s]));
    this.fig.querySelectorAll('[data-run]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.run === key)));
    // One tick per step, plus the start and the answer.
    const ids = [...new Set(this.beats.filter(b => b.step != null).map(b => b.step))];
    this.marksAt = [0, ...ids.map(id => this.beats.findIndex(b => b.step === id)), this.beats.length - 1];
    this.ticks.innerHTML = this.marksAt.map((k, j) =>
      `<li><button type="button" data-beat="${k}" aria-label="${j === 0 ? 'Start' : j === this.marksAt.length - 1 ? 'Answer' : `Step ${j}`}"></button></li>`).join('');
    this.ptr.hidden = true;
    this.fig.querySelector('[data-frame]').style.aspectRatio = `${run.size[0]} / ${run.size[1]}`;
    [...new Set(this.beats.map(b => b.f))].forEach(n => load(src(run, n)));
    this.show(0, false);
  };

  // The input display: what is sent to the machine, a click or keystrokes.
  Replay.prototype.input = function (kind, text) {
    this.hud.innerHTML = ICON[kind] + `<span>${esc(text)}</span>`;
    this.hud.className = `rp-hud on ${kind}`;
    clearTimeout(this.hudOff);
    this.hudOff = setTimeout(() => this.hud.classList.remove('on'), 800);
  };

  // A typed text, drawn into the field of the frame it was typed into.
  Replay.prototype.field = function (act, text, active) {
    const [w, h] = this.run.size, [x, y, bw, bh] = act.box;
    const d = document.createElement('div');
    d.className = `rp-field${bh > 60 ? ' area' : ''}${active ? ' active' : ''}`;
    Object.assign(d.style, { left: `${x / w * 100}%`, top: `${y / h * 100}%`, width: `${bw / w * 100}%`, height: `${bh / h * 100}%` });
    d.innerHTML = `<span></span>${active ? '<i></i>' : ''}`;
    d.firstChild.textContent = text;
    this.typed.append(d);
    return d;
  };

  /* Show beat i. Animated when it follows the beat shown now; returns how long its actions take. */
  Replay.prototype.show = function (i, animate) {
    this.clear();
    const run = this.run, b = this.beats[i], k = this.k;
    const P = { move: PACE.move * k, click: PACE.click * k, char: PACE.char * k, key: PACE.key * k, scroll: PACE.scroll * k };
    animate = animate && !reduced.matches;
    this.i = i;
    const last = i === this.beats.length - 1;
    const pos = at => { this.ptr.style.left = `${at[0] / run.size[0] * 100}%`; this.ptr.style.top = `${at[1] / run.size[1] * 100}%`; };

    // frame
    const url = src(run, b.f);
    if (this.img.dataset.url !== url) {
      this.img.dataset.url = url;
      load(url).then(() => { if (this.img.dataset.url === url) this.img.src = url; });
    }
    this.img.alt = `HospitalRun as the harness saw it at this point of the request “${run.request.split('\n')[0]}”`;

    // what was typed into this frame before this beat stays on it
    this.typed.textContent = '';
    for (let k = i - 1; k >= 0 && this.beats[k].f === b.f; k--) {
      for (const a of this.beats[k].acts || []) if (a.t === 'type' && a.box) this.field(a, a.text, false);
    }
    // where the pointer was before this beat
    let at = null;
    for (let k = i - 1; k >= 0 && !at; k--) {
      const c = (this.beats[k].acts || []).filter(a => a.t === 'click').pop();
      if (c) at = c.at;
    }
    const acts = b.acts || [];
    if (!at && !acts.some(a => a.t === 'click')) this.ptr.hidden = true;
    else { this.ptr.hidden = false; if (at) pos(at); }
    this.ptr.classList.remove('hit');
    this.ptr.style.transitionDuration = animate ? `${Math.round(P.move)}ms` : '0s';

    // the actions of this beat, one after the other
    let t = 0;
    for (const a of acts) {
      if (a.t === 'click') {
        if (!animate) { pos(a.at); continue; }
        this.later(t, () => pos(a.at));
        t += P.move;
        this.later(t, () => { this.ptr.classList.remove('hit'); void this.ptr.offsetWidth; this.ptr.classList.add('hit'); this.input('mouse', 'left click'); });
        t += P.click;
      } else if (a.t === 'type') {
        if (!a.box) { if (animate) { this.later(t, () => this.input('keys', a.text)); t += a.text.length * P.char + 120 * k; } continue; }
        if (!animate) { this.field(a, a.text, false); continue; }
        this.later(t, () => {
          const d = this.field(a, '', true);
          this.input('keys', a.text);
          [...a.text].forEach((_, n) => this.later(n * P.char, () => { d.firstChild.textContent = a.text.slice(0, n + 1); }));
        });
        t += a.text.length * P.char + 120 * k;
      } else if (animate) {
        const text = a.t === 'key' ? a.keys : `scroll ${a.dir}`;
        this.later(t, () => this.input(a.t === 'key' ? 'keys' : 'mouse', text));
        t += a.t === 'key' ? P.key : P.scroll;
      }
    }

    // what happens, in words
    this.say.className = `rp-say who-${b.who}${b.ok === false ? ' failed' : ''}`;
    this.say.firstChild.textContent = WHO[b.who];
    this.say.lastChild.textContent = b.say;

    this.console(b, last);

    let on = 0;
    this.marksAt.forEach((k, j) => { if (k <= i) on = j; });
    [...this.ticks.children].forEach((li, j) => {
      li.firstChild.classList.toggle('on', j === on);
      li.firstChild.classList.toggle('seen', j < on);
    });
    const n = this.marksAt.length - 2;
    this.count.textContent = last ? 'Answer' : on === 0 ? 'Start' : `Step ${on} of ${n}`;
    return t;
  };

  /* The console's view of this moment: request, steps with how each was checked, the answer. */
  Replay.prototype.console = function (b, last) {
    const run = this.run;
    const t = run.timing;
    const plan = b.plan.map(([id, st]) => ({ s: this.steps.get(id), st })).filter(p => p.s);
    const at = plan.findIndex(p => p.st === 'in_progress');
    const planning = !last && at < 0;
    const shown = plan.filter(p => p.st !== 'skipped');
    this.el.brand.className = `c-brand${last ? '' : ' running'}`;
    this.el.brand.innerHTML = last ? ICON.check : '<span>A</span>';
    this.el.title.textContent = last ? 'Done' : 'Working on it';
    const took = t.total < 60 ? `${Math.round(t.total)} s` : `${Math.floor(t.total / 60)} min ${Math.round(t.total % 60)} s`;
    this.el.sub.textContent = last ? `${shown.length} steps · ${took}`
      : at >= 0 ? (at + 1 < plan.length ? `Step ${at + 1} of ${plan.length}` : `Step ${at + 1}`)
      : b.who === 'planner' ? 'Planning' : plan.length ? 'Reading' : 'Starting';
    this.el.progress.innerHTML = last ? '<span class="c-seg c-seg-done"></span>'
      : plan.filter(p => p.st !== 'failed').map(p => `<span class="c-seg c-seg-${p.st === 'in_progress' ? 'running' : p.st === 'typed' ? 'done' : p.st}"></span>`).join('') +
        (planning ? '<span class="c-seg c-seg-running"></span>' : '');

    const row = (node, body, cur, lastRow) =>
      `<div class="c-row"${cur ? ' data-current' : ''}><div class="c-rail"><span class="c-node node-${node}">` +
      `${node === 'done' || node === 'typed' || node === 'result' ? ICON.check : node === 'failed' ? ICON.cross : node === 'running' || node === 'planning' ? ICON.spin : ''}</span>` +
      `${lastRow ? '' : `<span class="c-line${node === 'done' ? ' passed' : ''}"></span>`}</div><div class="c-body">${body}</div></div>`;
    const rows = [];
    plan.forEach((p, k) => {
      const s = p.s;
      const lastRow = last ? false : (k === plan.length - 1 && !planning);
      const commit = s.commits ? '<span class="c-chip warn">Saves or sends data</span>' : '';
      if (p.st === 'in_progress') {
        const phase = b.step === s.id && b.kind === 'act' ? b.say.replace(/\.$/, '')
          : b.step === s.id && b.kind === 'note' ? 'Closing what the field opened' : 'Looking at the screen';
        const chips = (s.by === 'decider' ? '<span class="c-chip">The model chose this click</span>' : '') + commit;
        rows.push(row('running', `<div class="c-now"><div class="c-nowtitle">${esc(s.text)}</div><div class="c-nowtext">${esc(phase)}</div>${chips ? `<div class="c-chips">${chips}</div>` : ''}</div>`, true, lastRow));
        return;
      }
      let note = '';
      if (p.st === 'done') note = s.note;
      else if (p.st === 'typed') note = 'Typed, not checked yet.';
      else if (p.st === 'failed') note = `Failed: ${s.note}`;
      const chips = commit && p.st === 'pending' ? `<div class="c-chips">${commit}</div>` : '';
      rows.push(row(p.st, `<div class="c-step step-${p.st}"><div class="c-text">${esc(s.text)}</div>${note ? `<div class="c-note">${esc(note)}</div>` : ''}${chips}</div>`, false, lastRow));
    });
    if (planning) {
      const what = b.who === 'planner' ? 'Planning the next steps' : !plan.length ? 'Reading the screen' : b.kind === 'decide' ? 'Asking the model' : 'Looking at the screen';
      rows.push(row('planning', `<div class="c-step"><div class="c-text">${what}</div></div>`, true, true));
    }
    if (last) {
      rows.push(row('result', `<div class="c-result"><div class="c-resultlabel">Done</div><div>${esc(run.result)}</div></div>` +
        `<div class="c-evidence">${esc(run.evidence.charAt(0).toUpperCase() + run.evidence.slice(1))}.</div>`, true, true));
    }
    this.thread.innerHTML = `<div class="c-bubble-line"><div class="c-bubble">${esc(run.request)}</div></div><div class="c-card">${rows.join('')}</div>`;
    const cur = this.thread.querySelector('[data-current]');
    // Keep the row that is being worked on in view, with as much of the list above it as fits.
    this.thread.scrollTop = cur ? Math.max(0, cur.offsetTop + cur.offsetHeight - this.thread.clientHeight + 16) : 0;
  };

  // A beat stays until its actions have run (`ran`, already stretched) and then for its hold.
  Replay.prototype.tick = function (ran) {
    clearTimeout(this.timer);
    if (!this.playing) return;
    const b = this.beats[this.i];
    this.timer = setTimeout(() => {
      let t;
      if (this.i >= this.beats.length - 1) {
        if (this.cycle) this.load(RUNS[(RUNS.indexOf(this.run) + 1) % RUNS.length].key);
        else this.show(0, false);
        t = 0;
      } else t = this.show(this.i + 1, true);
      this.tick(t);
    }, (ran || 0) + (b.kind === 'done' ? ANSWER : hold(b) * this.k));
  };
  Replay.prototype.play = function () {
    if (this.playing) return;
    this.playing = true; this.userPaused = false;
    this.say.setAttribute('aria-live', 'off');
    this.toggleBtn.innerHTML = ICON.pause; this.toggleBtn.setAttribute('aria-label', 'Pause');
    this.tick(0);
  };
  Replay.prototype.pause = function (byUser) {
    this.playing = false;
    if (byUser) this.userPaused = true;
    clearTimeout(this.timer);
    this.toggleBtn.innerHTML = ICON.play; this.toggleBtn.setAttribute('aria-label', 'Play');
  };

  // ------------------------------------------------------------------ what the harness reads on one frame
  /* layers: text, controls, changed (bool), ids (bool), only (rects that limit the text boxes),
     evidence ([labelText, valueText]). Returns the list the tooltip reads from. */
  function drawMarks(svg, run, n, layers) {
    const f = run.frames[n];
    svg.textContent = '';
    svg.setAttribute('viewBox', `0 0 ${run.size[0]} ${run.size[1]}`);
    if (!f) return [];
    const hot = [];
    const inside = (e, rects) => rects.some(r => e[0] + e[2] / 2 >= r[0] && e[0] + e[2] / 2 <= r[0] + r[2] && e[1] + e[3] / 2 >= r[1] && e[1] + e[3] / 2 <= r[1] + r[3]);
    if (layers.changed) {
      for (const r of f.changed) svg.append(svgEl('rect', { x: r[0], y: r[1], width: r[2], height: r[3], rx: 6, class: 'm-changed' }));
    }
    // A text's tag sits above it; a control's tag sits in its corner, so the label above the field stays readable.
    const tag = (x, y, text, cls, box) => {
      const w = text.length * 7.4 + 8;
      if (box) { x = box[0] + box[2] - w - 3; y = box[1] + 19; }
      const ty = y >= 17 ? y - 16 : y + 2;
      svg.append(svgEl('rect', { x, y: ty, width: w, height: 15, rx: 3, class: `m-tag ${cls}` }));
      const t = svgEl('text', { x: x + 4, y: ty + 11.5, class: 'm-tagtext' });
      t.textContent = text;
      svg.append(t);
    };
    for (const e of f.els) {
      const kind = e[4];
      if (kind === 2 && !layers.controls) continue;
      if (kind !== 2 && !layers.text) {
        if (!(layers.controls && kind === 1 && e[7])) continue;
      }
      if (layers.only && kind !== 2 && !inside(e, layers.only)) continue;
      if (layers.controls && kind === 1 && e[7]) {          // a filled field: the control that shows the text
        const c = e[7];
        const r = svgEl('rect', { x: c[0], y: c[1], width: c[2], height: c[3], rx: 5, class: 'm-box m-control' });
        r.dataset.i = hot.push({ id: e[5], text: e[6], what: 'control showing this text' }) - 1;
        svg.append(r);
        if (layers.ids) tag(c[0], c[1], e[5], 'm-control', c);
        if (!layers.text) continue;
      }
      const cls = kind === 2 ? 'm-control' : 'm-text';
      const r = svgEl('rect', { x: e[0], y: e[1], width: e[2], height: e[3], rx: kind === 2 ? 5 : 3, class: `m-box ${cls}` });
      r.dataset.i = hot.push({ id: e[5], text: e[6], what: kind === 2 ? 'control without text' : kind === 1 ? 'text shown by a control' : 'text' }) - 1;
      svg.append(r);
      if (layers.ids) tag(e[0], e[1], e[5], cls, kind === 2 ? e : null);
    }
    if (layers.evidence) {
      const find = t => f.els.find(e => e[6] === t);
      const lab = find(layers.evidence[0]);
      const val = find(layers.evidence[1]);
      if (lab && val) {
        const c = val[7] || val;
        svg.append(svgEl('rect', { x: lab[0] - 4, y: lab[1] - 3, width: lab[2] + 8, height: lab[3] + 6, rx: 5, class: 'm-box m-ok' }));
        svg.append(svgEl('rect', { x: c[0], y: c[1], width: c[2], height: c[3], rx: 6, class: 'm-box m-ok m-okfill' }));
        const x = lab[0] - 16;
        svg.append(svgEl('path', { d: `M${lab[0] - 4} ${lab[1] + lab[3] / 2}H${x}V${c[1] + c[3] / 2}H${c[0]}`, class: 'm-link' }));
        const text = `${layers.evidence[1]} is next to ${layers.evidence[0]}`;
        const w = text.length * 8.4 + 44;
        const bx = c[0] + c[2] - w, by = c[1] + c[3] + 8;
        svg.append(svgEl('rect', { x: bx, y: by, width: w, height: 26, rx: 13, class: 'm-badge' }));
        svg.append(svgEl('path', { d: `M${bx + 12} ${by + 13.5}l4 4 7.5-8`, class: 'm-badgecheck' }));
        const t = svgEl('text', { x: bx + 32, y: by + 18, class: 'm-badgetext' });
        t.textContent = text;
        svg.append(t);
      }
    }
    return hot;
  }

  function tooltips(svg, tip, getHot) {
    const hide = () => { tip.hidden = true; };
    svg.addEventListener('pointermove', e => {
      const r = e.target.closest('[data-i]');
      if (!r) return hide();
      const h = getHot()[Number(r.dataset.i)];
      if (!h) return hide();
      tip.innerHTML = `<b>${esc(h.id)}</b>${h.text ? `<span>${esc(h.text)}</span>` : ''}<i>${esc(h.what)}</i>`;
      tip.hidden = false;
      const box = svg.getBoundingClientRect();
      const x = Math.min(e.clientX - box.left + 12, box.width - tip.offsetWidth - 6);
      const y = e.clientY - box.top + 14;
      tip.style.left = `${Math.max(6, x)}px`;
      tip.style.top = `${y + tip.offsetHeight > box.height ? e.clientY - box.top - tip.offsetHeight - 8 : y}px`;
    });
    svg.addEventListener('pointerleave', hide);
  }

  const VIEWS = [
    {
      key: 'text', label: 'Text', frame: 2, layers: { text: true, ids: true },
      title: 'Every text gets an ID',
      body: f => `Text recognition reads everything written in the window: ${f.texts} texts on this empty form. Each keeps its ID from one look at the screen to the next, so a model can name an element instead of guessing coordinates.`,
    },
    {
      key: 'controls', label: 'Controls', frame: 3, layers: { controls: true, ids: true },
      title: 'Empty fields carry no text',
      body: f => `The detector finds the controls, also those that text recognition cannot see: ${f.controls} empty fields and icons on this form. It takes seconds on a CPU, so it runs only when a step needs it.`,
    },
    {
      key: 'changed', label: 'What changed', frame: 9, layers: { changed: true, text: true, onlyChanged: true },
      title: 'What an action changed',
      body: () => 'After every action the picture is compared with the one before. Typing the date of birth opened a calendar over the fields below. The harness sees the new texts next to the field and closes the calendar with Esc before it types anything else.',
    },
    {
      key: 'check', label: 'The check', frame: 10, layers: { evidence: ['Date Of Birth', '10/10/2000'] },
      title: 'A value next to its label',
      body: () => 'An entry counts only when its value is read next to the right label. This check is plain code, with no model involved, and it is what the console reports for each step.',
    },
  ];

  function Annot(fig) {
    const run = byKey(fig.dataset.annot) || RUNS[0];
    fig.querySelector('[data-views]').innerHTML = VIEWS.map((v, k) =>
      `<button type="button" role="tab" id="annot-tab-${v.key}" data-view="${k}" aria-selected="false" aria-controls="annot-panel" tabindex="-1">${v.label}</button>`).join('');
    const img = fig.querySelector('[data-img]'), svg = fig.querySelector('[data-marks]');
    const title = fig.querySelector('[data-title]'), body = fig.querySelector('[data-text]');
    let hot = [], cur = 0;
    tooltips(svg, fig.querySelector('[data-tip]'), () => hot);
    const show = k => {
      cur = k;
      const v = VIEWS[k], f = run.frames[v.frame];
      fig.querySelectorAll('[data-view]').forEach((b, j) => { b.setAttribute('aria-selected', String(j === k)); b.tabIndex = j === k ? 0 : -1; });
      fig.querySelector('#annot-panel').setAttribute('aria-labelledby', `annot-tab-${v.key}`);
      const url = src(run, v.frame);
      img.dataset.url = url;
      load(url).then(() => { if (img.dataset.url === url) img.src = url; });
      img.alt = `HospitalRun’s New Patient form as captured during the run, with the harness’s marks: ${v.title.toLowerCase()}`;
      const layers = Object.assign({}, v.layers);
      if (layers.onlyChanged) layers.only = f.changed;
      hot = drawMarks(svg, run, v.frame, layers);
      title.textContent = v.title;
      body.textContent = v.body({ texts: f.els.filter(e => e[4] !== 2).length, controls: f.els.filter(e => e[4] === 2).length });
    };
    fig.addEventListener('click', e => {
      const b = e.target.closest('[data-view]');
      if (b) show(Number(b.dataset.view));
    });
    fig.querySelector('[data-views]').addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const k = (cur + (e.key === 'ArrowRight' ? 1 : VIEWS.length - 1)) % VIEWS.length;
      show(k);
      fig.querySelector(`[data-view="${k}"]`).focus();
    });
    show(0);
  }

  // ------------------------------------------------------------------ wiring
  if (!RUNS.length) return;
  const replays = new Map();
  document.querySelectorAll('[data-replay]').forEach(fig => replays.set(fig, new Replay(fig)));
  document.querySelectorAll('[data-annot]').forEach(fig => Annot(fig));
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      const p = replays.get(e.target);
      if (e.isIntersecting) { if (!reduced.matches && !p.userPaused) p.play(); } else p.pause(false);
    }), { threshold: 0.3 });
    replays.forEach((_, fig) => io.observe(fig));
  }
})();
