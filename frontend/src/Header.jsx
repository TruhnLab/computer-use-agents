import { useEffect, useRef } from 'react';
import { formatClock, formatDuration, keyLabel, visibleSteps } from './runModel';
import { CheckIcon, CloseIcon, CrossIcon, PauseIcon, PlayIcon, PointerIcon, SettingsIcon, StopIcon } from './icons';

// Status on the left; Pause and Stop on the right, always in the same place
// and disabled, not hidden, when there is nothing to pause or stop.

// `state` drives the badge at the top left, so that the state of the run can
// be read at a glance: working, paused, your turn, done, stopped, problem.
export function statusOf(run, outcome, elapsed) {
  const steps = visibleSteps(run);
  const total = steps.length;
  const doneCount = steps.filter((t) => t.status === 'done' || t.status === 'failed').length;
  const at = steps.findIndex((t) => t.status === 'in_progress') + 1;
  const clock = formatClock(elapsed);

  if (run.phase === 'idle') return { state: 'idle', title: 'Ready', sub: 'No request running' };

  if (run.phase === 'running') {
    // The planner often plans one screen at a time, so the number of steps
    // is only named while steps beyond the current one are known.
    const where = at ? (at < total ? `Step ${at} of ${total}` : `Step ${at}`) : '';
    if (run.intent === 'stopping') return { state: 'stopped', title: 'Stopping', sub: 'Press Stop again to force it' };
    if (run.paused) return { state: 'paused', title: 'Paused', sub: [where && `At ${where.toLowerCase()}`, clock].filter(Boolean).join(' · ') };
    if (run.intent === 'pausing') return { state: 'running', title: 'Pausing', sub: [where, clock].filter(Boolean).join(' · ') };
    let doing = where;
    // Short on purpose: the narrow window has room for about twenty characters.
    if (!at) doing = run.planning ? 'Planning' : (total ? 'Reading' : 'Starting');
    return { state: 'running', title: 'Working on it', sub: `${doing} · ${clock}` };
  }

  const took = formatDuration(elapsed);
  const stepsText = `${total} ${total === 1 ? 'step' : 'steps'}`;
  switch (outcome && outcome.kind) {
    case 'done': {
      // Something was set aside on the way: not the same as done.
      const left = (outcome.open || []).length;
      if (left) return { state: 'partial', title: 'Partly done', sub: [`${left} not done`, took].filter(Boolean).join(' · ') };
      return { state: 'done', title: 'Done', sub: [stepsText, took].filter(Boolean).join(' · ') };
    }
    case 'question': return { state: 'question', title: 'Your turn', sub: doneCount ? `After step ${doneCount} · ${clock}` : 'Before the first step' };
    case 'stopped': return { state: 'stopped', title: 'Stopped', sub: [`${doneCount} ${doneCount === 1 ? 'step' : 'steps'} done`, took].filter(Boolean).join(' · ') };
    // Unattended: the agent would have asked, and there was nobody to ask.
    // (A short title: the narrow window cuts off anything longer.)
    case 'unanswered': return { state: 'partial', title: 'Open question', sub: ['Nobody to ask', took].filter(Boolean).join(' · ') };
    case 'aborted': return { state: 'problem', title: 'Could not finish', sub: [stepsText, took].filter(Boolean).join(' · ') };
    case 'error': return { state: 'problem', title: 'Something went wrong', sub: 'See the message below' };
    default: return { state: 'problem', title: 'Ended', sub: 'No checked result' };
  }
}

function Badge({ state }) {
  let icon = <PointerIcon />;
  if (state === 'done') icon = <CheckIcon size={18} />;
  else if (state === 'paused') icon = <PauseIcon size={15} />;
  else if (state === 'question') icon = <span>?</span>;
  else if (state === 'partial') icon = <span>!</span>;
  else if (state === 'stopped') icon = <StopIcon size={13} />;
  else if (state === 'problem') icon = <CrossIcon size={16} />;
  return <div className={`brand brand-${state}`} aria-hidden="true">{icon}</div>;
}

export function Header({ run, status, onPause, onResume, onStop, onSettings, settingsOpen, extra }) {
  const running = run.phase === 'running';
  const stopping = run.intent === 'stopping';
  return (
    <header className="header">
      <Badge state={status.state} />
      <div className="status" role="status" aria-live="polite">
        <div className="status-title">{status.title}</div>
        <div className="status-sub">{status.sub}</div>
      </div>
      {extra}
      <button type="button" className="icon-btn" aria-label="Settings" aria-expanded={settingsOpen} onClick={onSettings}>
        <SettingsIcon />
      </button>
      {run.paused ? (
        <button type="button" className="round-btn round-btn-primary" aria-label="Resume" title="Resume"
          onClick={onResume} disabled={!running || stopping || run.intent === 'resuming'}>
          <PlayIcon />
        </button>
      ) : (
        <button type="button" className="round-btn" aria-label="Pause" title="Pause"
          onClick={onPause} disabled={!running || stopping || run.intent === 'pausing'}>
          <PauseIcon />
        </button>
      )}
      <button type="button" className="stop-btn" onClick={onStop} disabled={!running}
        title={stopping ? 'Stop at once, without waiting for the agent' : 'Stop the agent (Esc)'}>
        <StopIcon />
        <span>{stopping ? 'Force' : 'Stop'}</span>
      </button>
    </header>
  );
}

// One segment per step while the plan is short; a single bar when it is long.
export function Progress({ run, finished, partial }) {
  // The bar says how far the request has come. An attempt that failed and was
  // replaced by another step is not progress and not an alarm either: it is
  // left out here and stays visible in the list of steps.
  const steps = visibleSteps(run).filter((t) => t.status !== 'failed');
  const running = run.phase === 'running';
  // Between two steps (the next one is being planned) the bar ends in an open,
  // moving segment, so it never looks finished while the agent still works.
  const open = running && !steps.some((t) => t.status === 'in_progress' || t.status === 'pending');
  // A request that is done shows one full bar, whatever happened on the way.
  if (finished) {
    return (
      <div className="progress" role="img" aria-label={partial ? 'Partly done' : 'Done'}>
        <div className="progress-bar"><div className={`progress-fill${partial ? ' progress-fill-partial' : ''}`} style={{ width: '100%' }} /></div>
      </div>
    );
  }
  if (!steps.length && !open) return <div className="progress progress-empty" aria-hidden="true" />;
  const done = steps.filter((t) => t.status === 'done').length;
  const label = running ? `${done} ${done === 1 ? 'step' : 'steps'} done, still working` : `${done} of ${steps.length} steps done`;
  if (steps.length > 14) {
    return (
      <div className="progress" role="img" aria-label={label}>
        <div className="progress-bar"><div className="progress-fill" style={{ width: `${(done / steps.length) * 100}%` }} /></div>
      </div>
    );
  }
  return (
    <div className="progress" role="img" aria-label={label}>
      {steps.map((t) => {
        let tone = t.status;
        if (t.status === 'in_progress') tone = run.phase === 'ended' ? 'halted' : run.paused ? 'paused' : 'running';
        return <span key={t.id} className={`seg seg-${tone}`} />;
      })}
      {open && <span className={`seg seg-${run.paused ? 'paused' : 'running'}`} />}
    </div>
  );
}

export function ModeSwitch({ debug, onChange }) {
  return (
    <div className="switch" role="group" aria-label="View">
      <button type="button" className={debug ? '' : 'on'} aria-pressed={!debug} onClick={() => onChange(false)}>Operator</button>
      <button type="button" className={debug ? 'on' : ''} aria-pressed={debug} onClick={() => onChange(true)}>Debug</button>
    </div>
  );
}

function ModelSelect({ id, label, hint, value, options, disabled, onChange }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
        ))}
      </select>
      {hint && <div className="field-hint">{hint}</div>}
    </div>
  );
}

const modelLabel = (m) => `${m.model}${m.available ? '' : ' (offline)'}`;

export function SettingsPanel({ run, debug, onMode, models, speed, unattended, onReset, onClose }) {
  const panel = useRef(null);
  const running = run.phase === 'running';

  useEffect(() => {
    const onDown = (e) => {
      if (panel.current && !panel.current.contains(e.target) && !e.target.closest('[aria-label="Settings"]')) onClose();
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [onClose]);

  // The planner writes the steps, so it must be a chat model. Any model can decide.
  const planners = models.models.filter((m) => !m.decision);
  const plannerOptions = planners.map((m) => ({ value: m.profile, label: modelLabel(m), disabled: !m.available }));
  if (!planners.some((m) => m.profile === models.planner)) {
    plannerOptions.unshift({ value: models.planner, label: models.planner || 'No model selected', disabled: true });
  }
  const deciderOptions = [
    { value: 'SAME', label: 'Same as the planner' },
    ...models.models.map((m) => ({
      value: m.profile, label: `${modelLabel(m)}${m.decision ? ' · decision model' : ''}`, disabled: !m.available,
    })),
  ];
  const keys = run.keys || {};

  return (
    <div className="settings" ref={panel} role="dialog" aria-label="Settings">
      <div className="settings-head">
        <h2>Settings</h2>
        <button type="button" className="icon-btn icon-btn-small" aria-label="Close settings" onClick={onClose}><CloseIcon /></button>
      </div>

      <div className="field">
        <span className="field-label">View</span>
        <ModeSwitch debug={debug} onChange={onMode} />
        <div className="field-hint">Debug adds each step's actions, the model's answers, timing and the raw log.</div>
      </div>

      <ModelSelect id="planner-model" label="Planner model" value={models.planner} options={plannerOptions}
        disabled={running || !models.loaded} onChange={(v) => models.select(v, 'planner')}
        hint="Writes the steps for each screen." />
      <ModelSelect id="decider-model" label="Decider model" value={models.decider || 'SAME'} options={deciderOptions}
        disabled={running || !models.loaded} onChange={(v) => models.select(v, 'decider')}
        hint={running ? 'Models can be changed when no request is running.' : 'Answers single questions during a step.'} />
      {models.error && <div className="field-error">{models.error}</div>}

      {/* The agent reads this once, when a request starts. */}
      <div className="field">
        <span className="field-label" id="supervision-label">Supervision</span>
        <div className="switch" role="group" aria-labelledby="supervision-label">
          <button type="button" className={unattended.on ? '' : 'on'} aria-pressed={!unattended.on}
            disabled={running} onClick={() => unattended.change(false)}>Attended</button>
          <button type="button" className={unattended.on ? 'on' : ''} aria-pressed={unattended.on}
            disabled={running} onClick={() => unattended.change(true)}>Unattended</button>
        </div>
        <div className="field-hint">
          {unattended.on
            ? 'Nobody is at the screen: the agent never asks and never pauses by itself. A request it cannot settle alone ends unfinished. Pause and Stop still work.'
            : 'Someone is at the screen: the agent asks when it is unsure, and pauses when you take the mouse or another window comes to the front.'}
          {running ? ' Can be changed when no request is running.' : ''}
        </div>
        {unattended.error && <div className="field-error">{unattended.error}</div>}
      </div>

      <div className="field">
        <label htmlFor="cursor-speed">Cursor speed <span className="field-value">{Math.round(speed.speed)}%</span></label>
        <input id="cursor-speed" type="range" min={speed.min} max="100" step="1" value={speed.speed}
          onChange={(e) => speed.change(parseFloat(e.target.value))} />
        <div className="field-hint">How fast the pointer travels to its target. Takes effect at once.</div>
      </div>

      <div className="field">
        <span className="field-label">Keys that work from any window</span>
        <div className="field-hint">
          {keys.pause ? <><kbd>{keyLabel(keys.pause)}</kbd> pauses and resumes. </> : 'No pause key is active. '}
          {keys.stop ? <><kbd>{keyLabel(keys.stop)}</kbd> stops.</> : 'No stop key is active.'}
          {' '}They are active while a request is running.
        </div>
      </div>

      <button type="button" className="btn btn-quiet btn-wide" onClick={onReset}>Start a new conversation</button>
    </div>
  );
}

// Unattended, the agent neither asks nor pauses by itself. That is easy to
// forget once it is set, so it is said next to the place where a request is
// typed for as long as it holds: for the run in progress, or for the next one.
export function UnattendedNote({ run, on, onChange }) {
  const running = run.phase === 'running';
  if (!(running ? run.unattended : on)) return null;
  return (
    <div className="mode-note" role="note">
      <span className="mode-note-mark" aria-hidden="true">!</span>
      <span className="mode-note-text">
        <strong>{running ? 'Unattended run.' : 'Unattended is on.'}</strong>
        {running ? ' The agent does not ask and does not pause by itself.' : ' The agent will not ask and will not pause by itself.'}
      </span>
      {!running && <button type="button" className="link-btn" onClick={onChange}>Change</button>}
    </div>
  );
}

// "Scroll Lock pauses, Pause stops, from any window": the keys only help if
// the operator knows them, and they are the way to stop without the mouse.
export function KeyHint({ run }) {
  const keys = run.keys || {};
  if (run.phase !== 'running' || (!keys.pause && !keys.stop)) return null;
  return (
    <div className="key-hint">
      {keys.pause && <><kbd>{keyLabel(keys.pause)}</kbd><span>{run.paused ? 'resumes' : 'pauses'}{keys.stop ? ',' : ''}</span></>}
      {keys.stop && <><kbd>{keyLabel(keys.stop)}</kbd><span>stops</span></>}
      <span>from any window</span>
    </div>
  );
}
