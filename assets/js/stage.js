/* A small step player for the explainer animations.

   A scene is a list of steps. Each step is a patch on the state before it, so
   any step can be rendered on its own (to scrub back and forth), and when the
   scene plays forward it is animated from the step before it: the pointer
   glides, text is typed, chips fly from the plan into the form, checks sweep
   over fields and plan items slide in and tick. */
(function () {
  'use strict';

  // Default timings (ms); a scene can override them with `speed: { type, move, fly, scan }`.
  const SPEED = { type: 42, move: 700, fly: 1000, scan: 750 };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');

  const WHO = { sup: 'Supervisor', act: 'Actioner', sys: 'Check', per: 'Perception', alarm: 'Wrong-field alarm', agent: 'Agent' };
  const GLYPH = { pending: '○', active: '▶', done: '✓', failed: '✗', obsolete: '−' };
  const MARKS = ['ok', 'bad', 'missing', 'new'];
  // Keys that describe one moment only and fall back to these defaults on the next step.
  const TRANSIENT = { boxes: [], click: false, alarm: false, press: null, retype: [], glow: null, fly: null, scan: null, planDelay: 0 };

  const clone = o => JSON.parse(JSON.stringify(o));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');   // `x` renders as code

  function fold(prev, patch) {
    const s = Object.assign(clone(prev), clone(TRANSIENT));
    s.fresh = s.uid;   // messages from this uid on were said in this step
    for (const [k, v] of Object.entries(patch)) {
      if (k === 'dur' || k === 'phase') continue;
      if (k === 'fields') for (const [f, fv] of Object.entries(v)) s.fields[f] = Object.assign({}, s.fields[f], fv);
      else if (k === 'say') for (const m of [].concat(v)) s.log.push(Object.assign({ uid: s.uid++ }, m));
      else if (k === 'plan') s.plan = typeof v === 'function' ? v(s.plan) : v;
      else s[k] = v;
    }
    return s;
  }

  const blank = () => Object.assign({ fields: {}, plan: [], log: [], task: null, at: null, cap: '', toast: '', request: '', count: '', uid: 0, fresh: 0 }, clone(TRANSIENT));

  // Plan edits, written as functions of the previous plan.
  const plan = {
    set: map => p => p.map(it => (it.id in map ? Object.assign({}, it, { st: map[it.id] }) : it)),
    add: (...items) => p => p.concat(items.map(it => Object.assign({ st: 'pending' }, it))),
    // A corrective item goes before the first open item, as in the harness's queue.
    front: item => p => {
      const n = p.slice();
      const i = n.findIndex(it => it.st === 'pending' || it.st === 'active');
      n.splice(i < 0 ? n.length : i, 0, Object.assign({ st: 'pending', corr: true }, item));
      return n;
    },
    seq: (...fns) => p => fns.reduce((acc, f) => f(acc), p),
  };

  // ------------------------------------------------------------------ stage

  function Stage(root, speed) {
    this.root = root;
    this.ms = Object.assign({}, SPEED, speed);
    this.screen = root.querySelector('[data-screen]');
    this.planEl = root.querySelector('[data-plan]');
    this.logEl = root.querySelector('[data-log]');
    this.taskEl = root.querySelector('[data-task]');
    this.toastEl = root.querySelector('[data-toast]');
    this.reqEl = root.querySelector('[data-request]');
    this.countEl = root.querySelector('[data-count]');
    this.packets = root.querySelector('[data-packets]');
    this.panes = [...root.querySelectorAll('[data-pane]')];
    this.max = this.logEl ? Number(this.logEl.dataset.max) || 3 : 0;
    this.fields = {};
    this.screen.querySelectorAll('[data-f]').forEach(el => { this.fields[el.dataset.f] = el; });

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
  Stage.prototype.clear = function () {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    if (this.packets) this.packets.textContent = '';
    this.overlay.querySelectorAll('.scan').forEach(n => n.remove());
  };

  // Render a state. With `prev`, animate the change from it. Returns how long the animation runs (ms).
  Stage.prototype.render = function (s, prev) {
    this.clear();
    const animate = !!prev && !reduced.matches;
    const moved = animate && prev.at !== s.at;
    const arrive = moved ? this.ms.move : 0;   // the screen reacts when the pointer gets there
    let busy = arrive;
    this.state = s;

    // A chip flies from the plan into the form; typing starts once it lands.
    let land = 0;
    if (animate && s.fly) { land = this.fly(s.fly); busy = Math.max(busy, land); }
    const typeAt = Math.max(arrive, land - 120) + 60;
    // Field marks (a verified badge, a red field) show once the pointer is there and any sweep has run.
    const markAt = animate ? arrive + (s.scan ? this.ms.scan : 0) : 0;

    if (this.reqEl) {
      const was = (prev && prev.request) || '';
      if (animate && was !== s.request) busy = Math.max(busy, this.type(this.reqEl, was, was, s.request, 0, this.ms.request));
      else this.reqEl.textContent = s.request;
    }

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
      if (markAt) this.later(markAt, look); else look();
      if (fs.v === undefined) continue;   // static fields keep their markup
      const val = el.querySelector('.val');
      const was = ps.v || '';
      if (animate && was !== fs.v) busy = Math.max(busy, this.type(val, was, s.retype.includes(f) ? '' : was, fs.v, typeAt));
      else val.textContent = fs.v;
    }

    this.point(s.at, animate);
    if (animate && s.click) {
      this.later(arrive, () => { this.cursor.classList.remove('click'); void this.cursor.offsetWidth; this.cursor.classList.add('click'); });
    }
    this.screen.querySelectorAll('[data-b]').forEach(b => b.classList.remove('press'));
    if (s.press) {
      const b = this.screen.querySelector(`[data-b="${s.press}"]`);
      if (animate) { this.later(arrive, () => b.classList.add('press')); this.later(arrive + 650, () => b.classList.remove('press')); }
    }
    if (this.toastEl) {
      const on = () => { this.toastEl.textContent = s.toast; this.toastEl.classList.toggle('on', !!s.toast); };
      if (animate && s.toast && !prev.toast) this.later(arrive + 350, on); else on();
    }
    if (this.countEl) {
      const changed = animate && prev.count !== s.count;
      const update = () => {
        this.countEl.textContent = s.count;
        this.countEl.hidden = !s.count;
        if (changed) { this.countEl.classList.remove('bump'); void this.countEl.offsetWidth; this.countEl.classList.add('bump'); }
      };
      if (changed && markAt) this.later(markAt, update); else update();
    }
    this.screen.classList.toggle('alarm', !!s.alarm);
    this.panes.forEach(p => p.classList.toggle('busy', p.dataset.pane === s.glow));

    // Boxes lock in when the pointer arrives, or after the verification sweep.
    let boxAt = arrive;
    if (animate && s.scan) { this.sweep(s.scan, arrive); boxAt = arrive + this.ms.scan; }
    if (animate && boxAt) { this.overlay.querySelectorAll('.box').forEach(n => n.remove()); this.later(boxAt, () => this.drawBoxes(s.boxes)); } else this.drawBoxes(s.boxes);
    busy = Math.max(busy, boxAt);

    const planAt = animate ? s.planDelay || 0 : 0;
    if (planAt) this.later(planAt, () => this.renderPlan(s.plan, true)); else this.renderPlan(s.plan, animate);
    busy = Math.max(busy, planAt);
    this.renderTask(s.task, prev && prev.task, animate);
    this.renderLog(s, animate);
    return busy;
  };

  // Animate a field from `shown` to `to`. At `delay` the field holds `from` (empty for a
  // select-all-and-retype), then it deletes back to the common prefix and types the rest.
  Stage.prototype.type = function (val, shown, from, to, delay, perChar) {
    const step = perChar || this.ms.type;
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
    [el.querySelector('.lbl'), el.querySelector('.inp')].forEach(p => {
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

  Stage.prototype.sweep = function (f, delay) {
    const el = this.fields[f];
    if (!el) return;
    this.later(delay, () => {
      const d = document.createElement('div');
      d.className = 'scan';
      d.style.setProperty('--scan', `${this.ms.scan}ms`);
      Object.assign(d.style, this.fieldRect(el));
      this.overlay.append(d);
      this.later(this.ms.scan + 60, () => d.remove());
    });
  };

  // Fly a chip from one element of the stage to another, on a slight arc. Returns when it lands.
  Stage.prototype.fly = function (f) {
    if (!this.packets || !('animate' in Element.prototype)) return 0;
    const a = this.root.querySelector(f.from);
    const b = this.root.querySelector(f.to);
    if (!a || !b) return 0;
    const chip = document.createElement('div');
    chip.className = `packet ${f.kind || ''}`;
    chip.innerHTML = fmt(f.text);
    this.packets.append(chip);
    const base = this.root.getBoundingClientRect();
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const w = chip.offsetWidth, h = chip.offsetHeight;
    const p0 = [ra.left - base.left + ra.width / 2 - w / 2, ra.top - base.top + ra.height / 2 - h / 2];
    const p1 = [rb.left - base.left + Math.min(rb.width / 2, w / 2 + 12) - w / 2, rb.top - base.top + rb.height / 2 - h / 2];
    const across = Math.abs(p1[0] - p0[0]) > Math.abs(p1[1] - p0[1]);
    const mid = [(p0[0] + p1[0]) / 2 + (across ? 0 : 40), (p0[1] + p1[1]) / 2 - (across ? 30 : 0)];
    const at = (p, k) => `translate(${p[0]}px, ${p[1]}px) scale(${k})`;
    const anim = chip.animate([
      { transform: at(p0, 0.6), opacity: 0 },
      { transform: at(p0, 1), opacity: 1, offset: 0.15 },
      { transform: at(mid, 1.06), opacity: 1, offset: 0.55 },
      { transform: at(p1, 1), opacity: 1, offset: 0.88 },
      { transform: at(p1, 0.7), opacity: 0 },
    ], { duration: this.ms.fly, easing: 'cubic-bezier(.4, 0, .2, 1)', fill: 'forwards' });
    anim.onfinish = () => chip.remove();
    return this.ms.fly;
  };

  Stage.prototype.relayout = function () {
    if (!this.state) return;
    this.point(this.state.at, false);
    this.drawBoxes(this.state.boxes);
  };

  // Keyed by item id, so status changes transition in place and only new items slide in.
  Stage.prototype.renderPlan = function (items, animate) {
    if (!this.planEl) return;
    const wrap = this.planEl.closest('.side-plan');
    if (wrap) wrap.hidden = items.length === 0;
    const have = new Map([...this.planEl.children].map(li => [li.dataset.id, li]));
    const keep = new Set(items.map(it => String(it.id)));
    have.forEach((li, id) => { if (!keep.has(id)) li.remove(); });
    let entering = 0;
    for (const it of items) {
      let li = have.get(String(it.id));
      const isNew = !li;
      const wasDone = !isNew && li.classList.contains('st-done');
      if (isNew) {
        li = document.createElement('li');
        li.dataset.id = it.id;
        li.innerHTML = '<span class="g" aria-hidden="true"></span><span class="t"></span>';
        if (animate) li.style.animationDelay = `${80 * entering++}ms`;   // several new items cascade in
      }
      const ticked = animate && !isNew && !wasDone && it.st === 'done';
      li.className = `st-${it.st}${it.corr ? ' corr' : ''}${isNew && animate ? ' enter' : ''}${ticked ? ' ticked' : ''}`;
      li.querySelector('.g').textContent = GLYPH[it.st] || '';
      li.querySelector('.t').textContent = it.text;
      if (it.corr && !li.querySelector('.badge')) {
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = 'fix';
        li.append(badge);
      }
      this.planEl.append(li);   // re-appending keeps the DOM in plan order
    }
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
        const from = m.who === 'sup' ? 'l' : m.who === 'act' ? 'r' : 'c';
        n.className = `msg ${m.who}${animate ? ` from-${from}` : ''}`;
        n.innerHTML =
          `<div class="mh"><b>${WHO[m.who]}</b>${m.to ? `<span class="to">→ ${esc(m.to)}</span>` : ''}</div>` +
          `<div class="mb">${fmt(m.text)}</div>` +
          (m.thought ? `<div class="th">${fmt(m.thought)}</div>` : '');
      }
      n.classList.toggle('old', m.uid < s.fresh);
      this.logEl.append(n);
    }
  };

  Stage.prototype.renderTask = function (task, prevTask, animate) {
    if (!this.taskEl) return;
    if (!task) {
      this.taskEl.className = 'task empty';
      this.taskEl.textContent = 'No item assigned yet';
      return;
    }
    this.taskEl.className = 'task';
    this.taskEl.innerHTML = task.map(([k, v]) => `<div class="row"><b>${esc(k)}</b><span>${fmt(v)}</span></div>`).join('');
    if (animate && JSON.stringify(task) !== JSON.stringify(prevTask)) { void this.taskEl.offsetWidth; this.taskEl.classList.add('flash'); }
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
    this.phase = -1;
    this.playing = false;
    this.userPaused = false;
    this.timer = 0;

    // With `phases`, the control row shows named chapters instead of one tick per step.
    const ctrl = fig.querySelector('[data-ctrl]');
    ctrl.innerHTML =
      '<div class="keys">' +
      `<button type="button" class="icon play" data-act="toggle" aria-label="Play">${ICON.play}</button>` +
      `<button type="button" class="icon" data-act="prev" aria-label="Previous step">${ICON.prev}</button>` +
      `<button type="button" class="icon" data-act="next" aria-label="Next step">${ICON.next}</button>` +
      '</div>' +
      (scene.phases
        ? `<ol class="phases">${scene.phases.map((p, k) => `<li><button type="button" data-phase="${k}"><span class="n">${k + 1}</span>${esc(p)}<span class="fill"></span></button></li>`).join('')}</ol>`
        : '<ol class="ticks"></ol><span class="count"></span>') +
      '<p class="cap" aria-live="off"></p>';
    this.toggleBtn = ctrl.querySelector('[data-act="toggle"]');
    this.ticks = ctrl.querySelector('.ticks');
    this.count = ctrl.querySelector('.count');
    this.phaseBtns = [...ctrl.querySelectorAll('[data-phase]')];
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
      else if (b.dataset.phase) {
        const k = this.steps.findIndex(st => st.phase === Number(b.dataset.phase));
        if (k >= 0) this.show(k, k === this.i + 1);
      }
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

    this.load(scene.variant || Object.keys(scene.variants)[0]);
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
    if (this.ticks) this.ticks.innerHTML = this.steps.map((_, k) => `<li><button type="button" data-step="${k}" aria-label="Step ${k + 1}"></button></li>`).join('');
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
    if (this.ticks) {
      [...this.ticks.querySelectorAll('button')].forEach((b, k) => {
        b.classList.toggle('on', k === i);
        b.classList.toggle('seen', k < i);
      });
      this.count.textContent = `${Math.max(0, i + 1)} / ${this.steps.length}`;
    }
    if (this.phaseBtns.length) {
      const ph = i < 0 ? -1 : this.steps[i].phase;
      const entered = ph !== this.phase;
      this.phase = ph;
      this.phaseBtns.forEach((b, k) => {
        b.classList.toggle('on', k === ph);
        b.classList.toggle('seen', k < ph);
        const fill = b.querySelector('.fill');
        // A chapter that keeps playing keeps its running fill; otherwise the fill is reset.
        if (k !== ph || entered || !this.playing) {
          fill.getAnimations().forEach(a => a.cancel());
          fill.classList.toggle('full', k === ph);
        }
      });
    }
    this.setIcon();
    return busy;
  };

  // While playing, the active chapter fills up over the time its steps take.
  Player.prototype.progress = function (ms) {
    const b = this.phaseBtns.find(x => x.classList.contains('on'));
    if (!b || reduced.matches || !('animate' in Element.prototype)) return;
    const fill = b.querySelector('.fill');
    fill.classList.remove('full');
    fill.getAnimations().forEach(a => a.cancel());
    fill.animate([{ width: '0%' }, { width: '100%' }], { duration: ms, easing: 'linear', fill: 'forwards' });
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

  Player.prototype.durOf = function (k, busy) {
    const st = this.steps[k];
    if (st.dur) return st.dur;
    const read = 1400 + this.states[k].cap.length * 30;
    return Math.min(9000, Math.max(read, busy + 1300));
  };

  Player.prototype.advance = function () {
    if (!this.playing) return;
    if (this.i >= this.steps.length - 1) {
      if (this.scene.loop) { this.show(-1, false); this.wait(900); } else this.pause(false);
      return;
    }
    const before = this.phase;
    const busy = this.show(this.i + 1, true);
    const ms = this.durOf(this.i, busy);
    if (this.phaseBtns.length && this.phase !== before) {
      let total = 0;
      for (let k = this.i; k < this.steps.length && this.steps[k].phase === this.phase; k++) total += k === this.i ? ms : (this.steps[k].dur || ms);
      this.progress(total);
    }
    this.wait(ms);
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
