/* A small step player for the two drawn replays of failure patterns.

   A scene is a list of steps. Each step is a patch on the state before it, so
   any step can be rendered on its own (to scrub back and forth), and when the
   scene plays forward it is animated from the step before it: the pointer
   glides, text is typed, and the panel beside the screen follows. For the
   harness that panel is its console (steps, and what happened during the step
   in the console's Debug look); for the study's agents it is a plain list of
   what the agent did. */
(function () {
  'use strict';

  // Default timings (ms); a scene can override them with `speed: { type, move }`.
  const SPEED = { type: 42, move: 700 };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');

  const WHO = { plan: 'Planner', dec: 'Decider', code: 'Code', sys: 'Check', per: 'Screen', agent: 'Agent' };
  const MARKS = ['ok', 'bad', 'missing', 'new'];
  const EVENTS = 4;   // lines of a step's log shown under it
  // Keys that describe one moment only and fall back to these defaults on the next step.
  const TRANSIENT = { boxes: [], click: false, press: null, retype: [] };

  const NODE = {
    done: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.4 3 3 6-6.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    running: '<svg class="spin" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2a5.8 5.8 0 1 1-5.8 5.8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
  };

  const clone = o => JSON.parse(JSON.stringify(o));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');   // `x` renders as code
  const activeId = plan => { const it = plan.find(p => p.st === 'active'); return it ? it.id : null; };

  function fold(prev, patch) {
    const s = Object.assign(clone(prev), clone(TRANSIENT));
    s.fresh = s.uid;   // messages from this uid on were said in this step
    const before = activeId(s.plan);
    const said = [];
    for (const [k, v] of Object.entries(patch)) {
      if (k === 'dur') continue;
      if (k === 'fields') for (const [f, fv] of Object.entries(v)) s.fields[f] = Object.assign({}, s.fields[f], fv);
      else if (k === 'say') for (const m of [].concat(v)) said.push(Object.assign({ uid: s.uid++ }, m));
      else if (k === 'plan') s.plan = typeof v === 'function' ? v(s.plan) : v;
      else s[k] = v;
    }
    // A message belongs to the step that was being worked on when it was said, or to the step it starts.
    const sid = before != null ? before : activeId(s.plan);
    for (const m of said) s.log.push(Object.assign(m, { sid }));
    return s;
  }

  const blank = () => Object.assign({ fields: {}, plan: [], log: [], at: null, cap: '', toast: '', uid: 0, fresh: 0 }, clone(TRANSIENT));

  // Plan edits, written as functions of the previous plan.
  const plan = {
    set: map => p => p.map(it => (it.id in map ? Object.assign({}, it, { st: map[it.id] }) : it)),
    // A step the planner puts before one that is still planned.
    before: (id, item) => p => {
      const n = p.slice();
      const i = n.findIndex(it => it.id === id);
      n.splice(i < 0 ? n.length : i, 0, Object.assign({ st: 'pending' }, item));
      return n;
    },
  };

  // ------------------------------------------------------------------ stage

  function Stage(root, speed) {
    this.root = root;
    this.ms = Object.assign({}, SPEED, speed);
    this.screen = root.querySelector('[data-screen]');
    this.logEl = root.querySelector('[data-log]');
    this.plainEl = root.querySelector('[data-plainlog]');
    this.consoleEl = root.querySelector('[data-console]');
    this.toastEl = root.querySelector('[data-toast]');
    this.max = this.logEl ? Number(this.logEl.dataset.max) || 3 : 0;
    this.fields = {};
    this.screen.querySelectorAll('[data-f]').forEach(el => { this.fields[el.dataset.f] = el; });

    if (this.consoleEl) {
      this.consoleEl.innerHTML =
        '<header class="c-head"><span class="c-brand running"><span>A</span></span><div class="c-status"><div class="c-title">Working on it</div><div class="c-sub"></div></div></header>' +
        '<div class="c-progress"></div><div class="c-thread"><div class="c-card"></div></div>';
      this.c = { sub: this.consoleEl.querySelector('.c-sub'), progress: this.consoleEl.querySelector('.c-progress'), thread: this.consoleEl.querySelector('.c-thread'), card: this.consoleEl.querySelector('.c-card') };
    }

    this.cursor = document.createElement('div');
    this.cursor.className = 'cursor';
    this.cursor.setAttribute('aria-hidden', 'true');
    this.cursor.innerHTML = '<svg viewBox="0 0 20 20"><path d="M2 1.5v15.2l4.1-3.9 2.6 5.9 2.7-1.2-2.6-5.8h5.7z" fill="#0e1626" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/></svg>';
    this.overlay = document.createElement('div');
    this.overlay.className = 'boxes';
    this.overlay.setAttribute('aria-hidden', 'true');
    this.screen.append(this.overlay, this.cursor);

    this.timers = [];
    this.state = null;
  }

  Stage.prototype.later = function (ms, fn) { this.timers.push(setTimeout(fn, ms)); };
  Stage.prototype.clear = function () { this.timers.forEach(clearTimeout); this.timers = []; };

  // Render a state. With `prev`, animate the change from it. Returns how long the animation runs (ms).
  Stage.prototype.render = function (s, prev) {
    this.clear();
    const animate = !!prev && !reduced.matches;
    const moved = animate && prev.at !== s.at;
    const arrive = moved ? this.ms.move : 0;   // the screen reacts when the pointer gets there
    let busy = arrive;
    this.state = s;

    for (const [f, el] of Object.entries(this.fields)) {
      const fs = s.fields[f] || {};
      const ps = (prev && prev.fields[f]) || {};
      const show = fs.show !== false;
      if (animate && show && ps.show === false) { el.classList.remove('appear'); void el.offsetWidth; el.classList.add('appear'); }
      el.hidden = !show;
      const look = () => {
        el.classList.toggle('focus', !!fs.focus);
        el.classList.toggle('popup', !!fs.popup);
        for (const m of MARKS) el.classList.toggle(m, fs.mark === m);
      };
      if (arrive) this.later(arrive, look); else look();
      if (fs.v === undefined) continue;   // static fields keep their markup
      const val = el.querySelector('.val');
      const was = ps.v || '';
      if (animate && was !== fs.v && el.classList.contains('sel')) {
        val.textContent = was;   // a dropdown choice appears at once, when the click lands
        const v = fs.v;
        this.later(arrive, () => { val.textContent = v; });
      } else if (animate && was !== fs.v) {
        busy = Math.max(busy, this.type(val, was, s.retype.includes(f) ? '' : was, fs.v, arrive + 60));
      } else val.textContent = fs.v;
    }

    this.point(s.at, animate);
    if (animate && s.click) {
      this.later(arrive, () => { this.cursor.classList.remove('click'); void this.cursor.offsetWidth; this.cursor.classList.add('click'); });
    }
    this.screen.querySelectorAll('[data-b]').forEach(b => b.classList.remove('press'));
    if (s.press && animate) {
      const b = this.screen.querySelector(`[data-b="${s.press}"]`);
      this.later(arrive, () => b.classList.add('press'));
      this.later(arrive + 650, () => b.classList.remove('press'));
    }
    if (this.toastEl) {
      const on = () => { this.toastEl.textContent = s.toast; this.toastEl.classList.toggle('on', !!s.toast); };
      if (animate && s.toast && !prev.toast) this.later(arrive + 350, on); else on();
    }

    // Boxes lock in when the pointer arrives.
    if (animate && arrive) { this.overlay.querySelectorAll('.box').forEach(n => n.remove()); this.later(arrive, () => this.drawBoxes(s.boxes)); } else this.drawBoxes(s.boxes);

    // The harness has its console; a study agent has the plain list of what it did.
    const harness = !!this.consoleEl && s.plan.length > 0;
    if (this.consoleEl) this.consoleEl.hidden = !harness;
    if (this.plainEl) this.plainEl.hidden = harness;
    if (harness) this.renderConsole(s, prev, animate); else this.renderLog(s, animate);
    return busy;
  };

  // Animate a field from `shown` to `to`. At `delay` the field holds `from` (empty for a
  // select-all-and-retype), then it deletes back to the common prefix and types the rest.
  Stage.prototype.type = function (val, shown, from, to, delay) {
    const step = this.ms.type;
    let p = 0;
    while (p < from.length && p < to.length && from[p] === to[p]) p++;
    const frames = [from];
    for (let i = from.length; i > p; i--) frames.push(from.slice(0, i - 1));
    for (let i = p + 1; i <= to.length; i++) frames.push(to.slice(0, i));
    val.textContent = shown;
    frames.forEach((t, i) => this.later(delay + i * step, () => { val.textContent = t; }));
    return delay + frames.length * step;
  };

  Stage.prototype.point = function (at, animate) {
    const base = this.screen.getBoundingClientRect();
    let x = base.width - 34, y = base.height - 26;   // parked in the corner
    const field = this.fields[at];
    const t = field ? field.querySelector('.inp') : at && this.screen.querySelector(`[data-b="${at}"]`);
    if (t && !t.closest('[hidden]')) {
      const r = t.getBoundingClientRect();
      x = r.left - base.left + (field ? Math.min(54, r.width * 0.35) : r.width * 0.55);
      y = r.top - base.top + r.height * 0.6;
    }
    this.cursor.style.transitionDuration = animate ? `${this.ms.move}ms` : '0s';
    this.cursor.style.transform = `translate(${Math.round(x - 2)}px, ${Math.round(y - 1.5)}px)`;
  };

  // A field's label and value, relative to the screen: what the label-anchored check looks at.
  Stage.prototype.fieldRect = function (el) {
    const base = this.screen.getBoundingClientRect();
    let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
    [el.querySelector('.lbl'), el.querySelector('.inp')].filter(Boolean).forEach(p => {
      const q = p.getBoundingClientRect();
      l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom);
    });
    return { left: `${l - base.left - 5}px`, top: `${t - base.top - 4}px`, width: `${r - l + 10}px`, height: `${b - t + 8}px` };
  };

  Stage.prototype.drawBoxes = function (boxes) {
    this.overlay.querySelectorAll('.box').forEach(n => n.remove());
    for (const b of boxes || []) {
      const el = this.fields[b.f];
      if (!el || el.hidden) continue;
      const d = document.createElement('div');
      d.className = `box ${b.kind || 'ok'}`;
      Object.assign(d.style, this.fieldRect(el));
      if (b.tag) {
        const g = document.createElement('span');
        g.className = 'tag';
        g.textContent = b.tag;
        d.append(g);
      }
      this.overlay.append(d);
    }
  };

  Stage.prototype.relayout = function () {
    if (!this.state) return;
    this.point(this.state.at, false);
    this.drawBoxes(this.state.boxes);
  };

  /* The console: the steps with how each was checked and, under the step that was worked on
     last, what happened during it (the console's Debug view). */
  Stage.prototype.renderConsole = function (s, prev, animate) {
    const items = s.plan;
    const at = items.findIndex(p => p.st === 'active');
    const lastMsg = s.log[s.log.length - 1];
    const cur = lastMsg ? lastMsg.sid : at >= 0 ? items[at].id : null;
    const events = [];
    for (let k = s.log.length - 1; k >= 0 && s.log[k].sid === cur && events.length < EVENTS; k--) events.unshift(s.log[k]);
    const between = lastMsg && cur == null;   // the models are asked between two steps
    const doing = !lastMsg ? 'Looking at the screen' : lastMsg.who === 'plan' ? 'Planning' : lastMsg.who === 'dec' ? 'Asking the model' : lastMsg.who === 'sys' ? 'Checking the result' : lastMsg.who === 'code' ? 'Working on the screen' : 'Looking at the screen';

    this.c.sub.textContent = at >= 0 ? `Step ${at + 1} of ${items.length}` : doing;
    this.c.progress.innerHTML = items.map(p => `<span class="c-seg c-seg-${p.st === 'active' ? 'running' : p.st}"></span>`).join('') +
      (between ? '<span class="c-seg c-seg-running"></span>' : '');

    const had = new Map(((prev && prev.plan) || []).map(p => [p.id, p.st]));
    const eventsHTML = events.length ? `<div class="c-events">${events.map(m =>
      `<div class="c-event${animate && m.uid >= s.fresh ? ' fresh' : ''}"><span class="c-tag who-${m.who}">${WHO[m.who].toLowerCase()}</span>` +
      `<span class="c-evtext">${fmt(m.text)}${m.thought ? `<i>${fmt(m.thought)}</i>` : ''}</span></div>`).join('')}</div>` : '';
    const row = (node, body, opts) =>
      `<div class="c-row${opts.cls || ''}"${opts.current ? ' data-current' : ''}><div class="c-rail"><span class="c-node node-${node}">${NODE[node] || ''}</span>` +
      `${opts.last ? '' : `<span class="c-line${node === 'done' ? ' passed' : ''}"></span>`}</div><div class="c-body">${body}</div></div>`;

    const rows = items.map((it, k) => {
      const mine = it.id === cur ? eventsHTML : '';
      const cls = animate && !had.has(it.id) ? ' enter' : animate && it.st === 'done' && had.get(it.id) !== 'done' ? ' ticked' : '';
      const opts = { cls, current: it.id === cur || (cur == null && it.st === 'active'), last: k === items.length - 1 && !between };
      if (it.st === 'active') {
        return row('running', `<div class="c-now"><div class="c-nowtitle">${esc(it.text)}</div><div class="c-nowtext">${doing === 'Planning' ? 'Starting' : doing}</div></div>${mine}`, opts);
      }
      return row(it.st, `<div class="c-step step-${it.st}"><div class="c-text">${esc(it.text)}</div>${it.st === 'done' && it.note ? `<div class="c-note">${esc(it.note)}</div>` : ''}</div>${mine}`, opts);
    });
    if (between) rows.push(row('running', `<div class="c-step"><div class="c-text">${doing}</div></div>${eventsHTML}`, { current: true, last: true }));
    this.c.card.innerHTML = rows.join('');
    const now = this.c.card.querySelector('[data-current]');
    // Keep the row in view that is being worked on, with as much of the list above it as fits.
    this.c.thread.scrollTop = now ? Math.max(0, now.offsetTop + now.offsetHeight - this.c.thread.clientHeight + 14) : 0;
  };

  Stage.prototype.renderLog = function (s, animate) {
    if (!this.logEl) return;
    const shown = s.log.slice(-this.max);
    const have = new Map([...this.logEl.children].map(n => [n.dataset.uid, n]));
    const keep = new Set(shown.map(m => String(m.uid)));
    have.forEach((n, uid) => { if (!keep.has(uid)) n.remove(); });
    for (const m of shown) {
      let n = have.get(String(m.uid));
      if (!n) {
        n = document.createElement('div');
        n.dataset.uid = m.uid;
        n.className = `msg ${m.who}${animate ? ' enter' : ''}`;
        n.innerHTML = `<div class="mh"><b>${WHO[m.who]}</b></div><div class="mb">${fmt(m.text)}</div>`;
      }
      n.classList.toggle('old', m.uid < s.fresh);
      this.logEl.append(n);
    }
  };

  // ----------------------------------------------------------------- player

  const ICON = {
    play: '<svg viewBox="0 0 14 14"><path d="M3 1.8v10.4L12 7z" fill="currentColor"/></svg>',
    pause: '<svg viewBox="0 0 14 14"><path d="M3 2h3v10H3zM8 2h3v10H8z" fill="currentColor"/></svg>',
    replay: '<svg viewBox="0 0 14 14"><path d="M7 2.2a4.8 4.8 0 1 1-4.6 6.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M1.6 1.6v4h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    prev: '<svg viewBox="0 0 14 14"><path d="M9 2.5 4.5 7 9 11.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    next: '<svg viewBox="0 0 14 14"><path d="M5 2.5 9.5 7 5 11.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  function Player(fig, scene) {
    this.fig = fig;
    this.scene = scene;
    this.stage = new Stage(fig.querySelector('.stage'), scene.speed);
    this.i = -1;
    this.playing = false;
    this.userPaused = false;
    this.timer = 0;

    const ctrl = fig.querySelector('[data-ctrl]');
    ctrl.innerHTML =
      '<div class="keys">' +
      `<button type="button" class="icon play" data-act="toggle" aria-label="Play">${ICON.play}</button>` +
      `<button type="button" class="icon" data-act="prev" aria-label="Previous step">${ICON.prev}</button>` +
      `<button type="button" class="icon" data-act="next" aria-label="Next step">${ICON.next}</button>` +
      '</div><ol class="ticks"></ol><span class="count"></span><p class="cap" aria-live="off"></p>';
    this.toggleBtn = ctrl.querySelector('[data-act="toggle"]');
    this.ticks = ctrl.querySelector('.ticks');
    this.count = ctrl.querySelector('.count');
    this.cap = ctrl.querySelector('.cap');

    ctrl.addEventListener('click', e => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.act === 'toggle') return this.playing ? this.pause(true) : this.play();
      this.pause(true);
      this.cap.setAttribute('aria-live', 'polite');
      if (b.dataset.act === 'prev') this.show(Math.max(-1, this.i - 1), false);
      else if (b.dataset.act === 'next') { if (this.i < this.steps.length - 1) this.show(this.i + 1, true); }
      else if (b.dataset.step) { const k = Number(b.dataset.step); this.show(k, k === this.i + 1); }
    });
    fig.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      if (e.target.closest('.seg')) return;
      e.preventDefault();
      ctrl.querySelector(`[data-act="${e.key === 'ArrowLeft' ? 'prev' : 'next'}"]`).click();
    });
    fig.querySelectorAll('[data-variant]').forEach(b => b.addEventListener('click', () => {
      this.load(b.dataset.variant);
      if (!reduced.matches) this.play();
    }));
    if ('ResizeObserver' in window) new ResizeObserver(() => this.stage.relayout()).observe(this.stage.screen);

    this.load(Object.keys(scene.variants)[0]);
  }

  Player.prototype.load = function (name) {
    this.pause(false);
    this.variant = name;
    const v = this.scene.variants[name];
    this.base = fold(fold(blank(), this.scene.base || {}), v.base || {});
    this.steps = v.steps;
    this.states = [];
    let s = this.base;
    for (const st of this.steps) { s = fold(s, st); this.states.push(s); }
    this.ticks.innerHTML = this.steps.map((_, k) => `<li><button type="button" data-step="${k}" aria-label="Step ${k + 1}"></button></li>`).join('');
    this.fig.querySelectorAll('[data-variant]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.variant === name)));
    this.i = -1;
    this.show(-1, false);
  };

  // Show step i (-1 is the starting state). Animated only when it follows the step shown now.
  Player.prototype.show = function (i, animate) {
    const prev = this.i < 0 ? this.base : this.states[this.i];
    const s = i < 0 ? this.base : this.states[i];
    const busy = this.stage.render(s, animate && i === this.i + 1 ? prev : null);
    this.i = i;
    this.cap.textContent = s.cap;
    [...this.ticks.querySelectorAll('button')].forEach((b, k) => {
      b.classList.toggle('on', k === i);
      b.classList.toggle('seen', k < i);
    });
    this.count.textContent = `${Math.max(0, i + 1)} / ${this.steps.length}`;
    this.setIcon();
    return busy;
  };

  Player.prototype.play = function () {
    if (this.playing) return;
    this.playing = true;
    this.userPaused = false;
    this.cap.setAttribute('aria-live', 'off');
    if (this.i >= this.steps.length - 1) this.show(-1, false);
    this.setIcon();
    this.wait(this.i < 0 ? 600 : 800);
  };

  Player.prototype.pause = function (byUser) {
    clearTimeout(this.timer);
    this.playing = false;
    if (byUser) this.userPaused = true;
    this.setIcon();
  };

  Player.prototype.wait = function (ms) {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.advance(), ms);
  };

  // A step stays as long as its caption takes to read, and at least until its animation is over.
  Player.prototype.durOf = function (k, busy) {
    const st = this.steps[k];
    if (st.dur) return st.dur;
    const read = 1400 + this.states[k].cap.length * 30;
    return Math.min(9000, Math.max(read, busy + 1300));
  };

  Player.prototype.advance = function () {
    if (!this.playing) return;
    if (this.i >= this.steps.length - 1) return this.pause(false);
    const busy = this.show(this.i + 1, true);
    this.wait(this.durOf(this.i, busy));
  };

  Player.prototype.setIcon = function () {
    if (!this.steps || !this.toggleBtn) return;   // not loaded yet
    const ended = !this.playing && this.i >= this.steps.length - 1;
    const kind = this.playing ? 'pause' : ended ? 'replay' : 'play';
    if (this.toggleBtn.dataset.kind === kind) return;
    this.toggleBtn.dataset.kind = kind;
    this.toggleBtn.innerHTML = ICON[kind];
    this.toggleBtn.setAttribute('aria-label', kind === 'pause' ? 'Pause' : kind === 'replay' ? 'Replay' : 'Play');
  };

  window.CUA = { Player, plan, reduced };
})();
