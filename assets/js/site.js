/* Page wiring: reveal on scroll, the explainer players, the failure-case tabs and the results chart. */
(function () {
  'use strict';
  const { Player, reduced } = window.CUA;
  const scenes = window.CUA_SCENES;
  const canObserve = 'IntersectionObserver' in window;

  // ---------- reveal on scroll ----------
  const reveals = document.querySelectorAll('.reveal');
  if (canObserve) {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
    }), { rootMargin: '0px 0px -8% 0px' });
    reveals.forEach(el => io.observe(el));
  } else reveals.forEach(el => el.classList.add('in'));

  // ---------- players: autoplay while in view, unless the reader paused or prefers less motion ----------
  const players = new Map();
  document.querySelectorAll('[data-scene]').forEach(fig => players.set(fig, new Player(fig, scenes[fig.dataset.scene])));

  const inView = new Set();
  const autoplay = fig => {
    const p = players.get(fig);
    if (!reduced.matches && !p.userPaused && inView.has(fig) && !fig.hidden) p.play();
  };
  if (canObserve) {
    const io = new IntersectionObserver(entries => entries.forEach(e => {
      if (e.isIntersecting) { inView.add(e.target); autoplay(e.target); }
      else { inView.delete(e.target); players.get(e.target).pause(false); }
    }), { threshold: 0.35 });
    players.forEach((_, fig) => io.observe(fig));
  }

  // ---------- failure-case tabs ----------
  const tabs = [...document.querySelectorAll('[data-tab]')];
  const select = tab => {
    tabs.forEach(t => {
      const on = t === tab;
      const fig = document.getElementById(t.dataset.tab);
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
      fig.hidden = !on;
      const p = players.get(fig);
      if (on) { p.stage.relayout(); p.userPaused = false; autoplay(fig); } else p.pause(false);
    });
  };
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => select(t));
    t.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      select(next);
    });
  });

  // ---------- results chart: bars built from the per-agent table, which doubles as the table view ----------
  const chart = document.getElementById('success-chart');
  if (chart) {
    const bars = chart.querySelector('[data-bars]');
    const tip = chart.querySelector('.tip');
    const rows = [...chart.querySelectorAll('[data-source] tbody tr')].map(tr => {
      const agents = [...tr.querySelectorAll('td')].map(td => parseFloat(td.textContent));
      return { group: tr.dataset.group, name: tr.querySelector('th').textContent, agents, mean: agents.reduce((a, b) => a + b, 0) / agents.length };
    });
    const lowest = Math.min(...rows.map(r => r.mean));
    const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text) n.textContent = text; return n; };

    const showTip = (row, r) => {
      tip.textContent = '';
      tip.append(el('b', '', `${Math.round(r.mean)}%`), el('div', '', r.name));
      const ag = el('div', 'ag');
      r.agents.forEach((v, j) => {
        const one = el('span');
        one.append(el('span', '', `Agent ${'ABC'[j]} `), document.createTextNode(`${v}%`));
        ag.append(one);
      });
      tip.append(ag);
      tip.hidden = false;
      const base = chart.getBoundingClientRect();
      const t = row.querySelector('.track').getBoundingClientRect();
      const x = Math.min(t.left - base.left + t.width * r.mean / 100 + 10, base.width - tip.offsetWidth - 8);
      const above = t.top - base.top - tip.offsetHeight - 6;
      tip.style.left = `${Math.max(8, x)}px`;
      tip.style.top = `${above > 0 ? above : t.bottom - base.top + 6}px`;
    };
    const hideTip = () => { tip.hidden = true; };

    let group = null;
    for (const r of rows) {
      if (r.group !== group) { group = r.group; bars.append(el('div', 'grp', group)); }
      const row = el('div', 'row');
      row.tabIndex = 0;
      row.setAttribute('aria-label', `${r.name}: ${Math.round(r.mean)}% success; agent A ${r.agents[0]}%, B ${r.agents[1]}%, C ${r.agents[2]}%`);
      const track = el('span', 'track');
      track.style.setProperty('--v', `${r.mean}%`);
      track.append(el('span', 'bar'));
      if (r.mean === lowest) track.append(el('span', 'val', `${Math.round(r.mean)}%`));   // label the extreme only
      row.append(el('span', 'name', r.name), track);
      row.addEventListener('pointerenter', () => showTip(row, r));
      row.addEventListener('focus', () => showTip(row, r));
      row.addEventListener('pointerleave', hideTip);
      row.addEventListener('blur', hideTip);
      bars.append(row);
    }
  }
})();
