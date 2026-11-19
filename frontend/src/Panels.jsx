import { useEffect, useRef, useState } from 'react';
import { clickPoint, formatDuration, visibleSteps } from './runModel';
import { ChevronIcon } from './icons';

// The agent's view, and the two debug panels: where the time went, the raw log.

const LEGEND = [
  ['ocr', 'Text that was read'],
  ['control', 'Control, including filled fields'],
  ['click', 'Last click'],
  ['changed', 'Changed since the last action'],
];

export function ScreenPane({ run, frames, debug }) {
  const [view, setView] = useState('raw');
  const [picked, setPicked] = useState(null);       // a step to look back at; null = live
  useEffect(() => { setPicked(null); }, [run.runId]);

  const steps = visibleSteps(run);
  const stepId = picked != null && frames.byStep[picked] ? picked : null;
  const shown = debug ? view : 'raw';
  const set = stepId != null ? frames.byStep[stepId] : frames.live;
  // The clean frame is the default; an older harness only writes the annotated one.
  const frame = (set && (set[shown] || set.raw || set.annotated)) || null;
  const isRaw = frame && set.raw === frame;

  const markStep = stepId != null ? stepId : (run.currentStepId != null ? run.currentStepId : run.lastStepId);
  const point = isRaw ? clickPoint(run.steps[markStep]) : null;
  const ordinal = (id) => steps.findIndex((t) => t.id === id) + 1;

  return (
    <section className="card pane">
      <div className="pane-head">
        <div className="pane-title">
          <h2>Agent's view</h2>
          {stepId != null ? (
            <span className="pane-pill">Step {ordinal(stepId) || '–'}</span>
          ) : run.phase === 'running' ? (
            <span className="pane-pill pane-pill-live"><i aria-hidden="true" />Live</span>
          ) : frame ? (
            <span className="pane-pill">Final screen</span>
          ) : null}
        </div>
        {debug && (
          <div className="switch switch-small" role="group" aria-label="Screenshot">
            <button type="button" className={view === 'raw' ? 'on' : ''} aria-pressed={view === 'raw'} onClick={() => setView('raw')}>Clean</button>
            <button type="button" className={view === 'annotated' ? 'on' : ''} aria-pressed={view === 'annotated'} onClick={() => setView('annotated')}>Annotated</button>
          </div>
        )}
      </div>

      <div className="screen">
        {frame ? (
          <div className="screen-frame" style={{
            aspectRatio: `${frame.width} / ${frame.height}`,
            maxWidth: `calc(min(62vh, 760px) * ${(frame.width / frame.height).toFixed(4)})`,
          }}>
            {/* One <img> for the whole run: only its source changes, to a frame
                that is already decoded, so nothing blanks between frames. */}
            <img src={frame.url} alt="The application as the agent sees it" draggable="false" />
            {point && (
              <span className="click-mark" style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
                role="img" aria-label="Where the agent clicked" />
            )}
          </div>
        ) : (
          <div className="screen-empty">
            {run.phase === 'running'
              ? 'The agent has not looked at the screen yet.'
              : 'The agent\'s view appears here while it works.'}
          </div>
        )}
      </div>

      {point && <div className="pane-note"><span className="click-key" aria-hidden="true" />Ring: where the agent clicked in this step</div>}

      {debug && shown === 'annotated' && frame && !isRaw && (
        <div className="legend">
          {LEGEND.map(([key, label]) => (
            <span key={key} className="legend-item"><span className={`swatch swatch-${key}`} aria-hidden="true" />{label}</span>
          ))}
        </div>
      )}

      {debug && steps.length > 0 && (
        <div className="scrub">
          <span className="scrub-label">Step</span>
          <button type="button" className={`scrub-btn scrub-live${stepId == null ? ' on' : ''}`} aria-pressed={stepId == null} onClick={() => setPicked(null)}>Live</button>
          {steps.map((t, i) => {
            const has = Boolean(frames.byStep[t.id]);
            return (
              <button type="button" key={t.id} disabled={!has}
                className={`scrub-btn${stepId === t.id ? ' on' : ''}${t.status === 'failed' ? ' failed' : ''}`}
                aria-pressed={stepId === t.id}
                aria-label={`Step ${i + 1}${t.status === 'failed' ? ', failed' : ''}${has ? '' : ', no screenshot kept'}`}
                title={t.text} onClick={() => setPicked(t.id)}>
                {i + 1}
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

const TIME_PARTS = [
  ['perception', 'Reading the screen'],
  ['planner', 'Planner'],
  ['decider', 'Decider'],
  ['input', 'Mouse and keyboard'],
];

export function Timing({ timing }) {
  if (!timing || !timing.seconds) return null;
  const parts = TIME_PARTS.map(([key, label]) => ({ key, label, value: Number(timing.seconds[key]) || 0 }));
  const other = Math.max(0, (Number(timing.total) || 0) - parts.reduce((a, p) => a + p.value, 0));
  parts.push({ key: 'other', label: 'Other', value: Math.round(other * 10) / 10 });
  const calls = timing.calls || {};
  const parses = timing.parses || {};
  const summary = parts.map((p) => `${p.label} ${p.value} seconds`).join(', ');
  return (
    <section className="card timing">
      <div className="timing-head">
        <div className="timing-total"><strong>{formatDuration(timing.total)}</strong><span>total time</span></div>
        <div className="timing-calls">{calls.planner || 0} planner calls · {calls.decider || 0} decider calls</div>
      </div>
      <div className="timing-bar" role="img" aria-label={summary}>
        {parts.filter((p) => p.value > 0).map((p) => (
          <span key={p.key} className={`time time-${p.key}`} style={{ flexGrow: p.value }} />
        ))}
      </div>
      <div className="timing-legend">
        {parts.map((p) => (
          <span key={p.key} className="legend-item"><span className={`dot time-${p.key}`} aria-hidden="true" />{p.label} {formatDuration(p.value) || '0 s'}</span>
        ))}
      </div>
      <div className="timing-extra">
        Screen reads: {parses.full || 0} full, {parses.partial || 0} partial, {parses.cached || 0} unchanged
        {parses.controls != null ? ` · control detection ${parses.controls} ×` : ''}
        {timing.tokens && timing.tokens.total != null ? ` · ${timing.tokens.total.toLocaleString('en-US')} tokens` : ''}
      </div>
    </section>
  );
}

export function RawLog({ lines }) {
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const pinned = useRef(true);

  // Follow the newest line unless the reader has scrolled up to read.
  useEffect(() => {
    if (open && pinned.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines.length, open]);

  return (
    <section className="card rawlog">
      <button type="button" className="fold fold-plain" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="fold-title">Raw log</span>
        <span className="fold-meta">{lines.length ? `${lines.length} lines` : 'empty'}</span>
        <ChevronIcon open={open} />
      </button>
      {open && (
        <pre className="log" ref={box} tabIndex={0}
          onScroll={(e) => {
            const el = e.currentTarget;
            pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
          }}>
          {lines.length ? lines.join('\n') : 'No lines yet.'}
        </pre>
      )}
    </section>
  );
}
