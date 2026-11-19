import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { api, logsUrl, screenshotUrl } from './api';
import { initialRun, outcomeOf, reduce, secondsBetween } from './runModel';

// ---------------------------------------------------------------------------
// The run: what was asked, the live log stream, and the controls.
// ---------------------------------------------------------------------------

export function useRun() {
  const [run, dispatch] = useReducer(reduce, undefined, initialRun);
  const [history, setHistory] = useState([]);   // earlier requests of this conversation
  const [error, setError] = useState('');
  const source = useRef(null);
  const runId = useRef(null);
  const replayUntil = useRef(0);                // lines below this index are a replay
  const generation = useRef(0);                 // drops answers of an attach() that was superseded
  const latest = useRef(run);
  latest.current = run;

  const closeStream = () => {
    if (source.current) { source.current.close(); source.current = null; }
  };

  // Read the backend's run and follow its log from the first line. This is the
  // one way in: after a page load, after a request was sent, and when another
  // window started a run.
  const attach = useCallback(async () => {
    const mine = ++generation.current;
    closeStream();
    let info;
    try {
      info = await api.run();
    } catch (_) {
      if (mine === generation.current) dispatch({ type: 'connection', ok: false });
      return false;
    }
    if (mine !== generation.current) return false;
    dispatch({ type: 'connection', ok: true });
    if (!info.task) {
      runId.current = null;
      dispatch({ type: 'clear' });
      dispatch({ type: 'keys', keys: info.keys });
      return true;
    }
    runId.current = info.run_id;
    replayUntil.current = info.lines;
    dispatch({
      type: 'start', runId: info.run_id, task: info.task,
      startedAt: info.started ? info.started * 1000 : Date.now(), keys: info.keys,
      unattended: Boolean(info.unattended),
    });

    const es = new EventSource(logsUrl());
    source.current = es;
    es.onopen = () => dispatch({ type: 'connection', ok: true });
    es.onmessage = (event) => {
      if (source.current !== es) return;
      const [rid, index] = String(event.lastEventId || '').split(':').map(Number);
      if (Number.isFinite(rid) && rid !== runId.current) {
        attach();                                 // a different run: start over with it
        return;
      }
      dispatch({ type: 'line', text: event.data, live: !(index < replayUntil.current) });
      if (event.data === '[DONE]') { es.close(); source.current = null; }
    };
    // The browser reconnects by itself and continues after the last line it got.
    es.onerror = () => dispatch({ type: 'connection', ok: false });
    return true;
  }, []);

  useEffect(() => {
    attach();
    return () => { generation.current += 1; closeStream(); };
  }, [attach]);

  // While nothing is attached (backend down at load, or idle), look again now
  // and then: the backend may have come up, or another window started a run.
  useEffect(() => {
    if (run.phase === 'running') return undefined;
    const id = setInterval(async () => {
      try {
        const info = await api.run();
        dispatch({ type: 'connection', ok: true });
        if (info.task && info.run_id !== runId.current) attach();
      } catch (_) {
        dispatch({ type: 'connection', ok: false });
      }
    }, 5000);
    return () => clearInterval(id);
  }, [run.phase, attach]);

  const submit = useCallback(async (text) => {
    const task = text.trim();
    const current = latest.current;
    if (!task || current.phase === 'running') return;
    setError('');
    const previous = current.task
      ? { task: current.task, outcome: outcomeOf(current), runId: current.runId }
      : null;
    try {
      await api.startTask(task);
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'The backend does not answer. Is it running?' : err.message);
      return;
    }
    if (previous) setHistory((h) => [...h, previous]);
    dispatch({ type: 'start', task });
    await attach();
  }, [attach]);

  const act = useCallback(async (intent, call) => {
    setError('');
    dispatch({ type: 'intent', intent });
    try {
      await call();
    } catch (err) {
      dispatch({ type: 'intent', intent: null });
      setError(err.message);
    }
  }, []);

  // The first Stop asks the agent to stop itself, so it can still say where
  // it was. A second Stop while that is pending kills the process.
  const stop = useCallback(() => {
    const current = latest.current;
    if (current.phase !== 'running') return;
    act('stopping', () => api.stop(current.intent === 'stopping'));
  }, [act]);
  const pause = useCallback(() => act('pausing', api.pause), [act]);
  const resume = useCallback(() => act('resuming', api.resume), [act]);

  const reset = useCallback(async () => {
    setError('');
    generation.current += 1;
    closeStream();
    try { await api.reset(); } catch (err) { setError(err.message); }
    runId.current = null;
    setHistory([]);
    dispatch({ type: 'clear' });
  }, []);

  return { run, history, error, setError, submit, stop, pause, resume, reset };
}

// Seconds the run has taken: ticking while it runs, the agent's own total
// once it has ended.
export function useElapsed(run) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (run.phase !== 'running') return undefined;
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [run.phase]);
  if (run.phase === 'idle') return 0;
  if (run.phase === 'ended') {
    if (run.timing && run.timing.total != null) return run.timing.total;
    return secondsBetween(run.firstTs, run.lastTs) || 0;
  }
  return Math.max(0, (Date.now() - run.startedAt) / 1000);
}

// ---------------------------------------------------------------------------
// The agent's view: screenshots that replace each other without flicker.
// ---------------------------------------------------------------------------
//
// The old console pointed an <img> at a new URL for every frame and hid it
// until it had loaded, so each frame blanked the view. Here a frame is fetched
// and decoded off screen first; the visible image changes only when the next
// one is ready to paint, and a frame that fails to load (the agent may be
// writing the file at that moment) leaves the previous one in place.

const HISTORY_STEPS = 20;

async function loadFrame(view, knownId) {
  let res;
  try {
    res = await fetch(screenshotUrl(view), { cache: 'no-store' });
  } catch (_) {
    return null;
  }
  if (!res.ok) return null;
  const id = res.headers.get('X-Frame-Id') || res.headers.get('Last-Modified') || '';
  if (id && id === knownId) return 'same';
  const url = URL.createObjectURL(await res.blob());
  const img = new Image();
  img.src = url;
  try {
    await img.decode();
  } catch (_) {
    URL.revokeObjectURL(url);
    return null;
  }
  return { url, id, width: img.naturalWidth, height: img.naturalHeight };
}

// `views`: which screenshots to keep up to date ('raw', 'annotated').
// `keepHistory`: also keep the last frame of every step, to look back at.
export function useFrames(run, views, keepHistory) {
  const [frames, setFrames] = useState({ live: {}, byStep: {} });
  const state = useRef({ live: {}, byStep: {}, order: [] });
  const busy = useRef(false);
  const again = useRef(false);
  const wanted = useRef({ views, keepHistory, step: 0 });
  wanted.current = { views, keepHistory, step: run.frameStep == null ? 0 : run.frameStep };
  const runKey = run.runId;

  const release = (url) => setTimeout(() => URL.revokeObjectURL(url), 4000);

  const refresh = useCallback(async () => {
    if (busy.current) { again.current = true; return; }
    busy.current = true;
    do {
      again.current = false;
      const { views: list, keepHistory: keep, step } = wanted.current;
      const st = state.current;
      for (const view of list) {
        let frame = await loadFrame(view, st.live[view] && st.live[view].id);
        if (frame === null) {
          await new Promise((r) => setTimeout(r, 250));     // half-written file: once more
          frame = await loadFrame(view, st.live[view] && st.live[view].id);
        }
        if (!frame || frame === 'same') continue;
        const old = st.live[view];
        st.live = { ...st.live, [view]: frame };
        if (keep) {
          const prev = st.byStep[step] && st.byStep[step][view];
          st.byStep = { ...st.byStep, [step]: { ...(st.byStep[step] || {}), [view]: frame } };
          if (!st.order.includes(step)) st.order.push(step);
          if (prev && prev.url !== frame.url) release(prev.url);
          while (st.order.length > HISTORY_STEPS) {
            const dropped = st.order.shift();
            Object.values(st.byStep[dropped] || {}).forEach((f) => release(f.url));
            const { [dropped]: _gone, ...rest } = st.byStep;
            st.byStep = rest;
          }
        } else if (old && old.url !== frame.url) {
          release(old.url);
        }
      }
      setFrames({ live: st.live, byStep: st.byStep });
    } while (again.current);
    busy.current = false;
  }, []);

  // A new run starts with an empty view: the previous run's last frame would
  // be mistaken for the current screen.
  useEffect(() => {
    const st = state.current;
    const urls = [...Object.values(st.live), ...Object.values(st.byStep).flatMap((v) => Object.values(v))];
    new Set(urls.map((f) => f.url)).forEach(release);
    state.current = { live: {}, byStep: {}, order: [] };
    setFrames({ live: {}, byStep: {} });
  }, [runKey]);

  useEffect(() => {
    if (run.frameSeq > 0) refresh();
  }, [run.frameSeq, refresh]);

  // Once per run and per set of views: a restored run shows what is on disk,
  // and a view that was not loaded so far (the mode changed) is fetched.
  const viewKey = views.join(',');
  useEffect(() => {
    if (runKey != null) refresh();
  }, [viewKey, runKey, refresh]);

  return frames;
}

// ---------------------------------------------------------------------------
// Settings kept by the backend (models, cursor speed) and by the browser (mode).
// ---------------------------------------------------------------------------

export function useModels() {
  const [state, setState] = useState({ models: [], planner: '', decider: 'SAME', loaded: false });
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await api.models();
      setState({
        models: data.models || [],
        planner: data.planner || '',
        decider: data.decider || 'SAME',
        loaded: true,
      });
    } catch (_) { /* the header shows the lost connection */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);           // availability changes
    return () => clearInterval(id);
  }, [load]);

  const select = useCallback(async (profile, role) => {
    setError('');
    setState((s) => ({ ...s, [role]: profile }));
    try {
      await api.selectModel(profile, role);
    } catch (err) {
      setError(err.message);
      load();
    }
  }, [load]);

  return { ...state, error, select, reload: load };
}

export function useCursorSpeed() {
  const [speed, setSpeed] = useState(30);
  const [min, setMin] = useState(1);
  useEffect(() => {
    api.cursorSpeed().then((d) => {
      if (typeof d.speed === 'number') setSpeed(d.speed);
      if (typeof d.min === 'number') setMin(d.min);
    }).catch(() => {});
  }, []);
  const change = useCallback((value) => {
    setSpeed(value);                                // the agent re-reads it mid-run
    api.setCursorSpeed(value).catch(() => {});
  }, []);
  return { speed, min, change };
}

// Unattended runs: whether the next request runs without anybody to ask. The
// backend holds the choice; it is read again whenever a run starts or ends
// (`phase`), because another window may have changed it.
export function useUnattended(phase) {
  const [on, setOn] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const data = await api.autonomous();
      setOn(Boolean(data.autonomous));
    } catch (_) { /* the header shows the lost connection */ }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load, phase]);

  const change = useCallback(async (value) => {
    setError('');
    setOn(value);
    try {
      await api.setAutonomous(value);
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'The backend does not answer. The setting was not changed.' : err.message);
      load();
    }
  }, [load]);

  return { on, error, change, reload: load };
}

export function useStoredState(key, fallback) {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key);
      return stored == null ? fallback : JSON.parse(stored);
    } catch (_) {
      return fallback;
    }
  });
  const set = useCallback((next) => {
    setValue(next);
    try { window.localStorage.setItem(key, JSON.stringify(next)); } catch (_) { /* private window */ }
  }, [key]);
  return [value, set];
}

export function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}
