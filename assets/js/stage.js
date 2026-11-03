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
  const TRANSIENT = { boxes: [], click: false, alarm: false, press: null, retype: [], glow: null, fly: null, scan: null, snap: false, planDelay: 0 };
  const SNAP_MS = 650;   // the screenshot flash and the perception boxes that follow it

  const KEY_ICON = '<svg viewBox="0 0 20 14" aria-hidden="true"><rect x="1" y="1" width="18" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M4.5 4.5h1M8 4.5h1M11.5 4.5h1M15 4.5h.5M4.5 7.5h1M8 7.5h1M11.5 7.5h1M15 7.5h.5M6 10.5h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
  const MOUSE_ICON = '<svg viewBox="0 0 14 20" aria-hidden="true"><rect x="1" y="1" width="12" height="18" rx="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M7 1v7M1 8h12" stroke="currentColor" stroke-width="1.5"/><path d="M1.6 7.2A6 6 0 0 1 7 1v7H1z" fill="currentColor" fill-opacity=".85"/></svg>';

  const clone = o => JSON.parse(JSON.stringify(o));
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');   // `x` renders as code

  // A drawn form field, for screens built from a spec: { f, label, kind, value, options }.
  // kind: 'sel' (dropdown, with `options` for its menu), 'date' (with a date picker),
  // 'static' (read-only), 'row' (a search hit), 'wide', 'tall'.
  const PICKER = '<div class="picker" aria-hidden="true"><div class="ph"><span>‹</span><b>October 2000</b><span>›</span></div>' +
    `<div class="pg">${'<i></i>'.repeat(9)}<i class="on"></i>${'<i></i>'.repeat(18)}</div></div>`;
  const fieldHTML = f =>
    `<div class="fld ${f.kind || ''}" data-f="${f.f}">` +
    (f.label ? `<span class="lbl">${esc(f.label)}</span>` : '') +
    `<span class="inp"><span class="val">${esc(f.value || '')}</span><span class="caret"></span></span>` +
    (/\bdate\b/.test(f.kind || '') ? PICKER : '') +
    (f.options ? `<div class="menu" aria-hidden="true">${f.options.map((o, k) => `<span data-o="${k}">${esc(o)}</span>`).join('')}</div>` : '') +
    '</div>';

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
    this.titleEl = root.querySelector('[data-title]');
    this.hud = root.querySelector('[data-hud]');   // what the agent sends: mouse clicks, keystrokes
    this.views = null;
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

  // Build the screen's views (one per application screen) from a spec; the teaser swaps examples this way.
  Stage.prototype.build = function (views) {
    const host = this.screen.querySelector('[data-views]');
    if (!host) return;
    host.textContent = '';
    this.views = {};
    for (const [key, v] of Object.entries(views)) {
      const el = document.createElement('div');
      el.className = 'view';
      el.dataset.view = key;
      el.innerHTML =
        `<div class="form">${v.fields.map(fieldHTML).join('')}</div>` +
        `<div class="btns">${(v.buttons || []).map(b => `<span class="b${b.ghost ? ' ghost' : ''}" data-b="${b.b}">${esc(b.text)}</span>`).join('')}</div>`;
      host.append(el);
      this.views[key] = { el, title: v.title };
    }
    this.fields = {};
    this.screen.querySelectorAll('[data-f]').forEach(el => { this.fields[el.dataset.f] = el; });
  };

  Stage.prototype.later = function (ms, fn) { this.timers.push(setTimeout(fn, ms)); };
  Stage.prototype.clear = function () {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    if (this.packets) this.packets.textContent = '';
    this.overlay.querySelectorAll('.scan, .perc').forEach(n => n.remove());
    this.screen.classList.remove('snap');
    if (this.hud) this.hud.classList.remove('on');
  };

  // The input display: a mouse click or the keystrokes being sent, shown briefly.
  Stage.prototype.input = function (kind, text) {
    if (!this.hud) return;
    this.hud.innerHTML = (kind === 'mouse' ? MOUSE_ICON : KEY_ICON) + `<span>${esc(text)}</span>`;
    this.hud.className = `hud on ${kind}`;
    clearTimeout(this.hudOff);
    this.hudOff = setTimeout(() => this.hud.classList.remove('on'), 900);
  };

  // A screenshot: a shutter flash, then the boxes perception finds on it (text in orange,
  // empty fields and buttons in blue), as in the harness's annotated screenshots.
  Stage.prototype.snapshot = function () {
    this.screen.classList.remove('snap');
    void this.screen.offsetWidth;
    this.screen.classList.add('snap');
    const base = this.screen.getBoundingClientRect();
    // Measure text itself (a label spans its whole field; its words do not).
    const rectOf = el => {
      if (!el.firstChild || el.firstChild.nodeType !== 3) return el.getBoundingClientRect();
      const r = document.createRange();
      r.selectNodeContents(el.firstChild);
      return r.getBoundingClientRect();
    };
    const box = (el, cls) => {
      if (!el || el.closest('[hidden]')) return;
      const q = rectOf(el);
      if (!q.width) return;
      const d = document.createElement('div');
      d.className = `perc ${cls}`;
      Object.assign(d.style, { left: `${q.left - base.left - 3}px`, top: `${q.top - base.top - 2}px`, width: `${q.width + 6}px`, height: `${q.height + 4}px` });
      this.overlay.append(d);
    };
    this.screen.querySelectorAll('.fld').forEach(f => {
      const val = f.querySelector('.val');
      box(f.querySelector('.lbl'), 'txt');
      if (val && val.textContent) box(val, 'txt'); else box(f.querySelector('.inp'), 'ico');
    });
    this.screen.querySelectorAll('.b').forEach(b => box(b, 'ico'));
    this.later(SNAP_MS, () => this.overlay.querySelectorAll('.perc').forEach(n => n.remove()));
  };

  // Render a state. With `prev`, animate the change from it. Returns how long the animation runs (ms).
  Stage.prototype.render = function (s, prev) {
    this.clear();
    const animate = !!prev && !reduced.matches;
    const moved = animate && prev.at !== s.at;
    const arrive = moved ? this.ms.move : 0;   // the screen reacts when the pointer gets there
    let busy = arrive;
    this.state = s;

    if (animate && s.snap) this.snapshot();

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
      const hover = s.at && s.at.startsWith(`${f}@`) ? s.at.split('@')[1] : null;   // pointer on a menu option
      const look = () => {
        el.classList.toggle('focus', !!fs.focus);
        el.classList.toggle('popup', !!fs.popup);
        for (const m of MARKS) el.classList.toggle(m, fs.mark === m);
        el.querySelectorAll('[data-o]').forEach(o => o.classList.toggle('on', o.dataset.o === hover));
      };
      if (markAt) this.later(markAt, look); else look();
      if (fs.v === undefined) continue;   // static fields keep their markup
      const val = el.querySelector('.val');
      const was = ps.v || '';
      if (animate && was !== fs.v && el.classList.contains('sel')) {
        val.textContent = was;   // a dropdown choice appears at once, when the click lands
        const v = fs.v;
        this.later(markAt, () => { val.textContent = v; });
      } else if (animate && was !== fs.v) {
        const from = s.retype.includes(f) ? '' : was;
        busy = Math.max(busy, this.type(val, was, from, fs.v, typeAt));
        // Echo the keystrokes: typed characters, or backspaces when the value shrinks.
        if (this.hud && !el.classList.contains('sel')) {
          let p = 0;
          while (p < from.length && p < fs.v.length && from[p] === fs.v[p]) p++;
          const typed = fs.v.slice(p), erased = from.length - p;
          if (erased) this.later(typeAt, () => this.input('keys', `⌫ × ${erased}`));
          if (typed) this.later(typeAt + erased * this.ms.type, () => this.input('keys', typed));
        }
      } else val.textContent = fs.v;
    }

    // Switch application screens (a click on a button that opens another view) when the pointer arrives.
    if (this.views) {
      const key = s.view || Object.keys(this.views)[0];
      const swap = () => {
        for (const [k, v] of Object.entries(this.views)) {
          const on = k === key;
          if (on && v.el.hidden && animate) { v.el.classList.remove('enter'); void v.el.offsetWidth; v.el.classList.add('enter'); }
          v.el.hidden = !on;
        }
        if (this.titleEl) this.titleEl.textContent = this.views[key].title;
      };
      if (animate && prev.view !== s.view && arrive) this.later(arrive + 180, swap); else swap();
    }

    this.point(s.at, animate);
    if (animate && s.click) {
      this.later(arrive, () => {
        this.cursor.classList.remove('click'); void this.cursor.offsetWidth; this.cursor.classList.add('click');
        this.input('mouse', 'left click');
      });
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
    // `at` names a field, a button (data-b), or a menu option as 'field@index'.
    const [name, opt] = String(at || '').split('@');
    const field = opt === undefined ? this.fields[name] : null;
    const t = field ? field.querySelector('.inp')
      : opt !== undefined ? this.fields[name] && this.fields[name].querySelector(`[data-o="${opt}"]`)
      : at && this.screen.querySelector(`.view:not([hidden]) [data-b="${at}"], [data-b="${at}"]`);
    if (t && !t.closest('[hidden]')) {
      const r = t.getBoundingClientRect();
      x = r.left - base.left + (field ? Math.min(54, r.width * 0.35) : opt !== undefined ? Math.min(40, r.width * 0.4) : r.width * 0.55);
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
      li.className = `st-${it.st}${it.corr ? ' corr' : ''}${it.rej ? ' rej' : ''}${isNew && animate ? ' enter' : ''}${ticked ? ' ticked' : ''}`;
      li.querySelector('.g').textContent = GLYPH[it.st] || '';
      li.querySelector('.t').textContent = it.text;
      // A badge marks a corrective item ('fix') or a claim the check just rejected ('rejected').
      const label = it.rej ? 'rejected' : it.corr ? 'fix' : '';
      let badge = li.querySelector('.badge');
      if (label && !badge) { badge = document.createElement('span'); li.append(badge); }
      if (badge) {
        if (label) { badge.className = `badge${it.rej ? ' no' : ''}`; badge.textContent = label; } else badge.remove();
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
      (scene.phases ? '<ol class="phases"></ol>' : '<ol class="ticks"></ol><span class="count"></span>') +
      '<p class="cap" aria-live="off"></p>';
    this.toggleBtn = ctrl.querySelector('[data-act="toggle"]');
    this.ticks = ctrl.querySelector('.ticks');
    this.count = ctrl.querySelector('.count');
    this.phaseList = ctrl.querySelector('.phases');
    this.phaseBtns = [];
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
    if (v.views) this.stage.build(v.views);
    // Chapter names may differ per variant (the teaser's examples each have their own).
    const phases = v.phases || this.scene.phases;
    if (this.phaseList && phases) {
      this.phaseList.innerHTML = phases.map((p, k) => `<li><button type="button" data-phase="${k}"><span class="n">${k + 1}</span>${esc(p)}<span class="fill"></span></button></li>`).join('');
      this.phaseBtns = [...this.phaseList.querySelectorAll('[data-phase]')];
      this.phase = -1;
    }
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
      if (this.scene.cycle) {   // on to the next variant, round and round
        const names = Object.keys(this.scene.variants);
        this.load(names[(names.indexOf(this.variant) + 1) % names.length]);
        this.play();
      } else if (this.scene.loop) { this.show(-1, false); this.wait(900); } else this.pause(false);
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
