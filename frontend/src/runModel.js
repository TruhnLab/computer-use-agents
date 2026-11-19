// Turns the agent's log lines into the state the console shows.
//
// A line is `HH:MM:SS | LEVEL | [MARKER] payload` when it comes from the agent
// (agent.py), or `[MARKER] payload` when the backend adds it around the run.
// The markers are the tags in square brackets that the harness (harness/) puts
// at the start of its log lines. Unknown markers and unknown JSON fields are
// ignored: the harness may add more.

const PREFIX = /^(\d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s?([\s\S]*)$/;
const MARKER = /^\[([A-Z_]+)\]\s?([\s\S]*)$/;
const MAX_LOG_LINES = 3000;

export function initialRun() {
  return {
    runId: null,
    task: '',
    phase: 'idle',            // idle | running | ended
    startedAt: null,          // ms, from the backend
    firstTs: null,            // seconds of day, first and last agent line
    lastTs: null,
    todos: [],                // the plan, as the agent last sent it
    steps: {},                // todo id -> { iter, events: [] }
    currentStepId: null,
    lastStepId: null,
    lastActionStep: null,
    openAction: null,         // an input action that has no result yet
    marks: [],                // planner rounds and breaks in the plan
    planning: false,
    retry: '',
    paused: false,
    pauseReason: '',
    intent: null,             // what the user just asked for: pausing | resuming | stopping
    keys: null,               // { stop, pause }: the agent's global keys
    issue: '',
    question: null,           // { text, options }
    backendQuestion: '',
    answer: '',
    result: '',
    evidence: [],             // texts found on screen that the result rests on
    evidenceNote: '',         // or what else it rests on, in the agent's words
    readingEvidence: false,
    loose: [],                // events that belong to the step or round that comes next
    open: [],                 // what was set aside so that the rest could go on
    replanned: null,          // the break the planner is being asked about
    unattended: false,        // nobody attends the run: it neither asks nor pauses by itself
    unanswered: '',           // what it would have asked when it ended for that reason
    refocused: false,         // it has just returned to its window by itself
    aborted: false,
    errors: [],
    stopped: null,            // { by, step, text } when the agent stopped itself
    killed: false,            // the process was terminated
    lastError: '',
    timing: null,
    rect: null,               // the captured window's place on screen
    frameSeq: 0,              // counts new screenshots seen live
    frameStep: null,
    log: [],
    connected: true,
  };
}

const parseJson = (text) => { try { return JSON.parse(text); } catch (_) { return null; } };

const secondsOfDay = (ts) => {
  const [h, m, s] = ts.split(':').map(Number);
  return h * 3600 + m * 60 + s;
};

export const secondsBetween = (a, b) => {
  if (a == null || b == null) return null;
  const d = b - a;
  return d < 0 ? d + 86400 : d;
};

// A step that has run or is running. Its id never changes again; a step still
// to come gets a new id whenever the planner puts other steps in front of it.
const started = (t) => t.status === 'done' || t.status === 'failed' || t.status === 'in_progress';
const lastStarted = (todos) => todos.reduce((acc, t) => (started(t) ? Math.max(acc, t.id) : acc), 0);

// A step keeps one `key` for as long as it is in the plan, whatever its id:
// a row that only got a new number is the same row and is not drawn anew.
function withKeys(list, before) {
  const known = new Map(before.map((t) => [t.id, t]));
  const ids = new Set(list.map((t) => t.id));
  const renumbered = before.filter((t) => t.status === 'pending' && !ids.has(t.id));
  return list.map((t) => {
    const same = known.get(t.id);
    if (same) return { ...t, key: same.key };
    const at = t.status === 'pending' ? renumbered.findIndex((old) => old.text === t.text) : -1;
    return { ...t, key: at >= 0 ? renumbered.splice(at, 1)[0].key : t.id };
  });
}

// ---- small helpers that change the draft `s` (a shallow copy of the state) ----

function stepOf(s, id) {
  return s.steps[id] || { iter: null, events: [] };
}

function patchStep(s, id, patch) {
  s.steps = { ...s.steps, [id]: { ...stepOf(s, id), ...patch } };
}

function addMark(s, mark) {
  s.marks = [...s.marks, {
    id: s.marks.length,
    type: 'plan',             // plan (first round) | replan (a break) | check (planner asked again) | aside
    // Shown after the last step that has run and before those still to come:
    // that is where the planner is asked, however many steps are still planned.
    afterId: lastStarted(s.todos),
    planned: false,           // a planner round has been announced for it
    supervise: [],
    assessment: '',
    startTs: null,
    endTs: null,
    events: [],
    ...mark,
  }];
}

// A planner round is every mark except the note that a step was set aside.
const isRound = (mark) => mark.type !== 'aside';

export function lastRound(marks) {
  for (let i = marks.length - 1; i >= 0; i -= 1) if (isRound(marks[i])) return marks[i];
  return null;
}

function patchMark(s, id, patch) {
  s.marks = s.marks.map((m) => (m.id === id ? { ...m, ...patch } : m));
}

function patchLastMark(s, patch) {
  const last = lastRound(s.marks);
  if (last) patchMark(s, last.id, patch);
}

// An event belongs to the step that is named or running. Without one it
// belongs to the planner round in progress. Without that either it is kept
// "loose" and handed to whatever comes next: the decider chooses the next
// click before that step exists, and a window is focused before the first
// planner round.
function addEvent(s, event, stepId) {
  const id = stepId != null ? stepId : s.currentStepId;
  if (id != null) {
    const step = stepOf(s, id);
    patchStep(s, id, { events: [...step.events, event] });
    return;
  }
  const round = s.planning ? lastRound(s.marks) : null;
  if (round) {
    patchLastMark(s, { events: [...round.events, event] });
    return;
  }
  s.loose = [...s.loose, event];
}

function agentLine(s, name, payload, ts, live) {
  switch (name) {
    case 'TODOS': {
      const list = parseJson(payload);
      if (!Array.isArray(list)) break;
      // After a failed step the steps still planned stay, and the planner may
      // drop them in its answer: they then arrive here as skipped, without a
      // [REPLAN] of their own. The break it was asked at is told.
      const was = new Map(s.todos.map((t) => [t.id, t.status]));
      const dropped = list.filter((t) => t.status === 'skipped' && was.get(t.id) === 'pending').map((t) => t.id);
      const asked = s.marks.find((m) => m.id === s.replanned);
      if (dropped.length && asked) patchMark(s, asked.id, { dropped: [...new Set([...asked.dropped, ...dropped])] });
      s.todos = withKeys(list, s.todos);
      const running = list.find((t) => t.status === 'in_progress');
      s.currentStepId = running ? running.id : null;
      if (running) s.planning = false;
      break;
    }
    case 'STEP': {
      const j = parseJson(payload);
      if (!j || j.id == null) break;
      s.currentStepId = j.id;
      s.lastStepId = j.id;
      s.planning = false;
      s.retry = '';
      s.replanned = null;
      // What led to this step (the decider's choice of it) is listed first.
      patchStep(s, j.id, { iter: j.iter, events: [...s.loose, ...stepOf(s, j.id).events] });
      s.loose = [];
      break;
    }
    case 'SUPERVISE': {
      const last = lastRound(s.marks);
      if (last && !last.planned) {
        patchLastMark(s, { planned: true, supervise: [payload], startTs: ts });
      } else {
        addMark(s, {
          type: s.marks.some((m) => isRound(m) && m.planned) ? 'check' : 'plan',
          planned: true, supervise: [payload], startTs: ts,
        });
      }
      if (s.loose.length) {
        const round = lastRound(s.marks);
        patchLastMark(s, { events: [...round.events, ...s.loose] });
        s.loose = [];
      }
      s.planning = true;
      s.retry = '';
      break;
    }
    case 'NAV': {
      if (payload.includes('objective:')) break;      // [STEP] says the same
      const assessment = payload.split('|').slice(1).join('|').trim();
      patchLastMark(s, { assessment, endTs: ts });
      s.planning = false;
      break;
    }
    case 'REPLAN': {
      const j = parseJson(payload) || {};
      if (j.reason === 'pause' && s.refocused) j.reason = 'focus';
      s.refocused = false;
      addMark(s, {
        type: 'replan', reason: j.reason || '', stepId: j.step != null ? j.step : null,
        detail: j.detail || '', dropped: j.dropped || [],
      });
      s.replanned = s.marks[s.marks.length - 1].id;
      break;
    }
    case 'OPEN': {
      // Always the full list of what was set aside in this request. A row is
      // added for the newest entry, at the place in the plan where it happened
      // and ahead of the planner round that is about to deal with it.
      const list = parseJson(payload);
      if (!Array.isArray(list)) break;
      const points = list.map(String).filter(Boolean);
      if (points.length > s.open.length && s.todos.some(started)) {
        const last = s.marks[s.marks.length - 1];
        const at = last && isRound(last) && !last.planned ? s.marks.length - 1 : s.marks.length;
        addMark(s, { type: 'aside', planned: true, text: points[points.length - 1] });
        const marks = s.marks.slice();
        marks.splice(at, 0, marks.pop());
        s.marks = marks;
      }
      s.open = points;
      break;
    }
    case 'ACTION': {
      const j = parseJson(payload);
      if (!j) break;
      const step = j.step != null ? j.step : s.currentStepId;
      addEvent(s, { type: 'action', action: j.action, args: j.args || {}, rect: s.rect }, step);
      s.lastActionStep = step;
      s.openAction = { step, action: j.action, args: j.args || {} };
      break;
    }
    case 'RESULT':
      if (payload.startsWith('verified')) {
        addEvent(s, { type: 'check', text: payload.replace(/^verified\s*[—-]?\s*/, '') });
      } else {
        addEvent(s, { type: 'result', text: payload }, s.lastActionStep);
        s.openAction = null;
      }
      break;
    case 'CHANGE':
      addEvent(s, { type: 'screen', text: payload }, s.lastActionStep);
      break;
    case 'PROGRESS':
      if (payload.includes('nochange')) addEvent(s, { type: 'screen', text: 'no visible change' });
      break;
    case 'DECISION': {
      const j = parseJson(payload);
      if (j) addEvent(s, { type: 'decision', ...j }, j.step);
      break;
    }
    case 'INTERVENE': {
      // A run that nobody attends has nobody to ask. A question of the planner
      // is handed back to it once; any other question ends the run.
      const own = /^nobody to ask\s*[—–-]\s*the planner decides itself:\s*([\s\S]*)$/i.exec(payload);
      const ended = /^nobody attends the run\b[^:]*:\s*it ends on what it would have asked:\s*([\s\S]*)$/i.exec(payload);
      if (own || ended) s.unattended = true;
      if (ended) s.unanswered = ended[1].trim();
      addEvent(s, { type: 'note', text: payload });
      // Shown to the operator where it happened: what the agent settled alone
      // is what a person will want to look at afterwards.
      if (own && own[1].trim()) addMark(s, { type: 'aside', kind: 'own', planned: true, text: own[1].trim() });
      break;
    }
    case 'FOCUS':
      // Unattended, the agent does not pause when another window comes to the
      // front: it returns to its own, and the [REPLAN] that follows is no pause.
      if (/nobody attends the run/i.test(payload)) {
        s.unattended = true;
        s.refocused = true;
      }
      addEvent(s, { type: 'note', text: `window ${payload}` });
      break;
    case 'RETRY':
      s.retry = payload;
      addEvent(s, { type: 'retry', text: payload });
      break;
    case 'ISSUE':
      s.issue = payload;
      // A failed step's reason is shown with the step (its note). An issue of
      // the run as a whole has no step to show it: keep it as a line in Debug.
      if (/decider does not answer/i.test(payload)) addEvent(s, { type: 'note', text: payload });
      break;
    case 'THOUGHT':
      // "decider · <question> → <choice>" repeats a [DECISION]. A thought
      // without an arrow stands alone ("the same click again — the planner
      // decides") and explains what happens next.
      if (!payload.includes('→')) addEvent(s, { type: 'note', text: payload });
      break;
    case 'QUESTION': {
      const j = parseJson(payload);
      s.question = j && typeof j === 'object' && j.text
        ? { text: j.text, options: Array.isArray(j.options) ? j.options : [] }
        : { text: payload, options: [] };
      break;
    }
    case 'TASK_RESULT':
      s.result = payload;
      break;
    case 'EVIDENCE': {
      // Three forms: a JSON list of the texts found on screen; the same as
      // "evidence found on screen: a; b; c" (which cannot carry a text with a
      // semicolon in it); or another reason as a sentence.
      const list = parseJson(payload);
      const found = /^evidence found on screen:\s*(.*)$/i.exec(payload);
      if (Array.isArray(list)) s.evidence = list.map(String).filter(Boolean);
      else s.evidence = found ? found[1].split(/;\s*/).filter(Boolean) : [];
      s.evidenceNote = Array.isArray(list) || found ? '' : payload;
      s.readingEvidence = false;
      break;
    }
    case 'TIMING':
      s.timing = parseJson(payload);
      break;
    case 'SCREENSHOT_READY': {
      const j = parseJson(payload);
      if (j && typeof j === 'object') s.rect = j;
      // Only a screenshot announced live is fetched: during the replay of a
      // run the file on disk is already a later one.
      if (live) {
        s.frameSeq += 1;
        s.frameStep = s.currentStepId != null ? s.currentStepId : (s.lastStepId || 0);
      }
      break;
    }
    case 'PAUSED':
      s.paused = true;
      s.pauseReason = payload;
      if (s.intent === 'pausing') s.intent = null;
      break;
    case 'RESUMED':
      s.paused = false;
      s.pauseReason = '';
      if (s.intent === 'resuming') s.intent = null;
      break;
    case 'STOPPED':
      s.stopped = parseJson(payload) || {};
      break;
    case 'KEYS': {
      const j = parseJson(payload);
      if (j) s.keys = { stop: j.stop || '', pause: j.pause || '' };
      break;
    }
    default:
      break;                                           // ITER, TODO, unknown
  }
}

// What the result rests on, for a harness that does not send [EVIDENCE] yet:
// it lists the texts it found on screen in plain log lines ("evidence check:
// 3/3 passed", then one "✓ '<text>'" each). [EVIDENCE] replaces what is read here.
function plainLine(s, msg) {
  if (/^\s*evidence check:/i.test(msg)) {
    s.evidence = [];
    s.readingEvidence = true;
    return;
  }
  if (!s.readingEvidence) return;
  const found = /^\s*✓\s+(.+?)\s*$/.exec(msg);
  if (found) s.evidence = [...s.evidence, found[1].replace(/^'(.*)'$/, '$1')];
  else if (!/^\s*✗/.test(msg)) s.readingEvidence = false;
}

function backendLine(s, name, payload) {
  switch (name) {
    case 'SYSTEM':
      if (/terminated by user/i.test(payload)) s.killed = true;
      break;
    case 'ANSWER': s.answer = payload; break;
    case 'QUESTION': s.backendQuestion = payload; break;
    case 'ABORTED': s.aborted = true; break;
    case 'ERROR': s.errors = [...s.errors, payload]; break;
    default: break;
  }
}

export function reduce(state, action) {
  switch (action.type) {
    case 'clear':
      return { ...initialRun(), keys: state.keys, connected: state.connected };
    case 'start':
      return {
        ...initialRun(),
        runId: action.runId != null ? action.runId : null,
        task: action.task || '',
        startedAt: action.startedAt || Date.now(),
        keys: action.keys || state.keys,
        unattended: Boolean(action.unattended),
        phase: 'running',
        connected: state.connected,
      };
    case 'keys':
      return state.phase === 'running' && state.keys ? state : { ...state, keys: action.keys };
    case 'intent':
      return { ...state, intent: action.intent };
    case 'connection':
      return state.connected === action.ok ? state : { ...state, connected: action.ok };
    case 'line': {
      const text = action.text;
      const s = { ...state };
      s.log = state.log.length >= MAX_LOG_LINES
        ? [...state.log.slice(-MAX_LOG_LINES + 1), text]
        : [...state.log, text];

      if (text === '[DONE]') {
        s.phase = 'ended';
        s.planning = false;
        s.paused = false;
        s.intent = null;
        return s;
      }

      const prefixed = PREFIX.exec(text);
      const msg = prefixed ? prefixed[3] : text;
      const marker = MARKER.exec(msg);
      if (prefixed) {
        const ts = secondsOfDay(prefixed[1]);
        if (s.firstTs == null) s.firstTs = ts;
        s.lastTs = ts;
        if (marker) agentLine(s, marker[1], marker[2].trim(), ts, action.live);
        else if (prefixed[2] === 'ERROR') s.lastError = msg.trim();
        else plainLine(s, msg);
      } else if (marker) {
        backendLine(s, marker[1], marker[2].trim());
      }
      return s;
    }
    default:
      return state;
  }
}

// ---- what the views read ----

// How a run ended. `null` while it is running or before the first request.
export function outcomeOf(run) {
  if (run.phase !== 'ended') return null;
  if (run.errors.length) return { kind: 'error', text: run.errors[0].split('\n')[0] };
  if (run.stopped || run.killed) return { kind: 'stopped' };
  // Unattended, a run that would have asked ends instead: not a failure of a
  // step, and not a question either, since nobody is expected to answer.
  if (run.aborted && run.unanswered) return { kind: 'unanswered', text: run.unanswered };
  if (run.aborted) return { kind: 'aborted', text: run.lastError || run.issue };
  if (run.question || run.backendQuestion) {
    return {
      kind: 'question',
      text: run.question ? run.question.text : run.backendQuestion,
      options: run.question ? run.question.options : [],
    };
  }
  if (run.answer || run.result) {
    // The answer names what was set aside ("... NOT DONE: a; b"). The list
    // itself comes with [OPEN]; the text is the fallback when it did not.
    const [text, rest] = splitOpen(run.answer || run.result);
    const open = run.open.length ? run.open : (rest ? [rest] : []);
    return { kind: 'done', text: text || 'The rest of the request was carried out.', open };
  }
  return { kind: 'empty' };
}

export function splitOpen(text) {
  const found = /^([\s\S]*?)\s*\bNOT DONE:\s*([\s\S]*)$/.exec(text || '');
  return found ? [found[1].trim(), found[2].trim()] : [text || '', ''];
}

// "Fill 'Payment Profile' with 'Private' — 3 ways failed, the last one: ..."
// → what was set aside, and why.
export function splitPoint(point) {
  const text = String(point);
  const at = text.indexOf(' — ');
  return at < 0 ? [text, ''] : [text.slice(0, at), text.slice(at + 3)];
}

// The first sentence of a note. A failure note often goes on with advice for
// the planner ("... Set row to a text that only its row shows"), which is not
// for the operator. A full stop inside a quoted text ('Dr. Doe') ends nothing.
export function leadSentence(text) {
  const t = String(text || '');
  let quotes = 0;
  for (let i = 0; i < t.length - 1; i += 1) {
    if (t[i] === "'") quotes += 1;
    else if (t[i] === '.' && /\s/.test(t[i + 1]) && quotes % 2 === 0 && i >= 12) return t.slice(0, i);
  }
  return t.replace(/\.$/, '');
}

// Steps that were dropped by a replan are not part of the count.
export const visibleSteps = (run) => run.todos.filter((t) => t.status !== 'skipped');

// The plan in display order: steps, with the planner rounds and breaks
// between them. Dropped steps are listed only when `withDropped`.
export function buildTimeline(run, withDropped) {
  const items = [];
  let next = 0;
  let ordinal = 0;
  for (const todo of run.todos) {
    while (next < run.marks.length && run.marks[next].afterId < todo.id) {
      items.push({ kind: 'mark', mark: run.marks[next] });
      next += 1;
    }
    if (todo.status === 'skipped') {
      if (withDropped) items.push({ kind: 'step', todo, ordinal: null });
      continue;
    }
    ordinal += 1;
    items.push({ kind: 'step', todo, ordinal });
  }
  for (; next < run.marks.length; next += 1) items.push({ kind: 'mark', mark: run.marks[next] });
  return items;
}

// What a planner round is for, read from the note the agent gives the planner.
export function roundTitle(mark, active) {
  if (mark.type === 'replan') {
    if (!active) return replanTitle(mark);
    if (mark.reason === 'failed_step') return 'Finding another way';
    if (mark.reason === 'pause' || mark.reason === 'focus') return 'Looking at the screen again';
    return 'Checking the plan against the screen';
  }
  if (mark.type === 'plan') return active ? 'Reading the screen and planning the steps' : 'Planned the steps';
  const note = mark.supervise[0] || '';
  if (/completion check did NOT/i.test(note)) return active ? 'Checking what is still missing' : 'Checked what was still missing';
  if (/^REJECTED/i.test(note)) return active ? 'Asking the planner again' : 'Asked the planner again';
  return active ? 'Planning the next steps' : 'Planned the next steps';
}

// A fill step that was typed without a look at the screen is `done` before
// its value has been read back: its note is the harness's "typed; …" sentence
// until the next look, when the note becomes the usual one or the step fails
// after all. (Recognised by that wording until the harness sends a field.)
export const awaitsCheck = (todo) => todo.status === 'done'
  && (todo.checked === false || /^typed;/i.test(todo.note || ''));

// Did this break come from a step that failed after later steps had run?
// (A typed value that turned out not to have arrived.)
export function failedLate(mark, todos) {
  return mark.type === 'replan' && mark.reason === 'failed_step' && mark.stepId != null
    && todos.some((t) => t.id > mark.stepId && t.id <= mark.afterId && t.status !== 'skipped');
}

// "First sentence. The rest." → the first sentence as the heading. The
// agent's questions can be several sentences long.
export function splitLead(text) {
  const found = /^([\s\S]{12,180}?[.?!])\s+([\s\S]+)$/.exec(text || '');
  return found ? [found[1], found[2]] : [text || '', ''];
}

// Why the agent paused, for the operator.
export function pauseSentence(reason) {
  const other = /another application's window came to the front:\s*'?(.*?)'?\s*$/i.exec(reason || '');
  if (other) return `Paused because another window came to the front${other[1] ? ` (${other[1]})` : ''}.`;
  if (/pointer/i.test(reason || '')) return 'Paused because the pointer was moved by hand.';
  return 'Paused.';
}

export function replanTitle(mark) {
  switch (mark.reason) {
    // The steps still planned stay unless the planner drops them in its answer.
    case 'failed_step': return (mark.dropped || []).length ? 'Replanned after the failed step' : 'Looked for another way';
    case 'unforeseen': return (mark.dropped || []).length ? 'Plan adjusted to an unexpected screen' : 'Unexpected screen, plan checked';
    case 'pause': return 'Replanned after the pause';
    // Unattended: another window came to the front and the agent went back to its own.
    case 'focus': return (mark.dropped || []).length ? 'Replanned after another window came to the front' : 'Another window came to the front, plan checked';
    default: return 'Replanned';
  }
}

const quote = (text, max = 40) => {
  const t = String(text == null ? '' : text);
  return `'${t.length > max ? `${t.slice(0, max)}…` : t}'`;
};

const keyName = (k) => {
  const names = { ctrl: 'Ctrl', alt: 'Alt', shift: 'Shift', win: 'Win', enter: 'Enter', escape: 'Esc',
    tab: 'Tab', backspace: 'Backspace', delete: 'Delete', space: 'Space', scrolllock: 'Scroll Lock',
    pause: 'Pause', insert: 'Insert', home: 'Home', end: 'End', pageup: 'Page Up', pagedown: 'Page Down' };
  const low = String(k).toLowerCase();
  return names[low] || (low.length === 1 ? low.toUpperCase() : low.charAt(0).toUpperCase() + low.slice(1));
};

// 'ctrl+alt+s' -> 'Ctrl + Alt + S'
export const keyLabel = (spec) => String(spec || '').split(/[+\s]+/).filter(Boolean).map(keyName).join(' + ');

// What the agent is doing, for the operator: "Typing 'John'".
export function describeAction(action, args = {}) {
  switch (action) {
    case 'click': return 'Clicking';
    case 'double_click': return 'Double-clicking';
    case 'type':
    case 'type_text': return `Typing ${quote(args.text)}`;
    case 'click_and_type': return `Clicking and typing ${quote(args.text)}`;
    case 'keypress': return `Pressing ${(args.keys || []).map(keyName).join(' + ') || 'a key'}`;
    case 'scroll': return 'Scrolling';
    case 'wait': return 'Waiting';
    case 'delete_chars': return 'Deleting text';
    default: return String(action || 'Working');
  }
}

// The same action for the debug view: "click (460, 141)".
export function actionText(action, args = {}) {
  if ((action === 'click' || action === 'double_click') && args.x != null) return `${action} (${args.x}, ${args.y})`;
  if (action === 'type' || action === 'type_text') return `type ${quote(args.text, 80)}`;
  if (action === 'keypress') return `keypress ${(args.keys || []).join(' + ')}`;
  if (action === 'wait') return `wait ${args.seconds != null ? `${args.seconds} s` : ''}`.trim();
  const rest = JSON.stringify(args);
  return rest === '{}' ? String(action) : `${action} ${rest}`;
}

// What the decider was asked, in the operator's words.
export function decisionPhrase(question) {
  switch (question) {
    case 'target': return 'which element to use';
    case 'outcome': return 'whether this worked';
    case 'screen': return 'whether the plan still fits';
    case 'complete': return 'whether the request is complete';
    case 'window': return 'which window to use';
    case 'next': return 'what to click next';
    case 'commits': return 'whether this saves or deletes something';
    case 'loop': return 'whether the request is still getting on';
    default: return 'what to do';
  }
}

export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '';
  if (seconds < 60) return `${seconds < 10 ? Math.round(seconds * 10) / 10 : Math.round(seconds)} s`;
  const m = Math.floor(seconds / 60);
  const rest = Math.round(seconds - m * 60);
  return `${m} min ${rest} s`;
}

export function formatClock(seconds) {
  const total = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

// The last click of a step as a point in the screenshot (0..1), or null.
// [ACTION] coordinates are screen pixels; the screenshot shows one window,
// whose place on screen comes with [SCREENSHOT_READY].
export function clickPoint(step) {
  if (!step) return null;
  for (let i = step.events.length - 1; i >= 0; i -= 1) {
    const ev = step.events[i];
    if (ev.type !== 'action' || ev.args.x == null || !ev.rect || !ev.rect.w || !ev.rect.h) continue;
    const x = (ev.args.x - ev.rect.x) / ev.rect.w;
    const y = (ev.args.y - ev.rect.y) / ev.rect.h;
    return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? { x, y } : null;
  }
  return null;
}
