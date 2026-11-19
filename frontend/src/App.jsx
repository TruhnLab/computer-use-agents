import { useEffect, useRef, useState } from 'react';
import { outcomeOf } from './runModel';
import { useCursorSpeed, useElapsed, useFrames, useMediaQuery, useModels, useRun, useStoredState, useUnattended } from './hooks';
import { Header, KeyHint, ModeSwitch, Progress, SettingsPanel, UnattendedNote, statusOf } from './Header';
import { Thread } from './Timeline';
import { RawLog, ScreenPane, Timing } from './Panels';
import { ChevronIcon, CloseIcon, SendIcon } from './icons';

// The operator's console. Two modes: Operator shows the request, the plan and
// the controls; Debug adds what we need while developing the agent. The window
// is a companion to the application the agent works in, so it is laid out for
// a narrow window first and gains a second column when it is wide.

function Composer({ run, outcome, onSubmit }) {
  const [text, setText] = useState('');
  const field = useRef(null);
  const running = run.phase === 'running';
  const question = Boolean(outcome && outcome.kind === 'question');

  useEffect(() => {
    if (!running && field.current) field.current.focus();
  }, [running, question]);

  const grow = () => {
    const el = field.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  };
  useEffect(grow, [text]);

  const send = () => {
    if (running || !text.trim()) return;
    onSubmit(text);
    setText('');
  };

  let placeholder = 'Describe what the agent should do';
  if (running) placeholder = 'Stop the agent to send a new request';
  else if (question) placeholder = 'Answer the agent';
  else if (run.task) placeholder = 'What should I do next?';

  return (
    <form className={`composer${question ? ' composer-asked' : ''}`} onSubmit={(e) => { e.preventDefault(); send(); }}>
      <label htmlFor="request" className="visually-hidden">{question ? 'Your answer' : 'Your request'}</label>
      <textarea id="request" ref={field} rows={1} value={text} disabled={running} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
        }} />
      <button type="submit" className="send-btn" aria-label={question ? 'Send answer' : 'Send'} disabled={running || !text.trim()}>
        <SendIcon />
      </button>
    </form>
  );
}

function ModelChips({ models, onOpen }) {
  if (!models.loaded) return null;
  const name = (profile) => {
    const found = models.models.find((m) => m.profile === profile);
    return found ? found.model : (profile || 'none');
  };
  const decider = !models.decider || models.decider === 'SAME' ? 'same as planner' : name(models.decider);
  return (
    <div className="model-chips">
      <button type="button" className="model-chip" onClick={onOpen}><span>Planner</span><strong>{name(models.planner)}</strong></button>
      <button type="button" className="model-chip" onClick={onOpen}><span>Decider</span><strong>{decider}</strong></button>
    </div>
  );
}

export default function App() {
  const { run, history, error, setError, submit, stop, pause, resume, reset } = useRun();
  const [debug, setDebug] = useStoredState('console.debug', false);
  const [viewOpen, setViewOpen] = useStoredState('console.view', false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const wide = useMediaQuery('(min-width: 900px)');
  const models = useModels();
  const speed = useCursorSpeed();
  const unattended = useUnattended(run.phase);

  const outcome = outcomeOf(run);
  const elapsed = useElapsed(run);
  const status = statusOf(run, outcome, elapsed);
  const running = run.phase === 'running';

  // Screenshots are fetched only while the view is on screen; the debug view
  // also keeps the annotated frame and the last frame of every step.
  const paneShown = wide || viewOpen;
  const frames = useFrames(run, paneShown ? (debug ? ['raw', 'annotated'] : ['raw']) : [], debug && paneShown);

  // Esc stops the agent while this window has the focus. The agent's own
  // global keys do the same from any window.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      if (settingsOpen) setSettingsOpen(false);
      else if (running) stop();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [settingsOpen, running, stop]);

  // The window is often behind the application: say in its title when it
  // needs the operator.
  useEffect(() => {
    document.title = run.phase === 'idle' ? 'Agent Console' : `${status.title} · Agent Console`;
  }, [run.phase, status.title]);

  const newConversation = () => {
    if (running && !window.confirm('A request is running. Stop it and start a new conversation?')) return;
    setSettingsOpen(false);
    reset();
  };

  const pane = <ScreenPane run={run} frames={frames} debug={debug} />;
  const debugPanels = debug && (<><Timing timing={run.timing} /><RawLog lines={run.log} /></>);

  return (
    <div className={`app${wide ? ' wide' : ''}${debug ? ' debug' : ''}`}>
      <div className="top">
        <Header run={run} status={status} onPause={pause} onResume={resume} onStop={stop}
          settingsOpen={settingsOpen}
          onSettings={() => { if (!settingsOpen) { models.reload(); unattended.reload(); } setSettingsOpen(!settingsOpen); }}
          extra={wide ? (
            <>
              {debug && <ModelChips models={models} onOpen={() => setSettingsOpen(true)} />}
              <ModeSwitch debug={debug} onChange={setDebug} />
            </>
          ) : null} />
        {settingsOpen && (
          <SettingsPanel run={run} debug={debug} onMode={setDebug} models={models} speed={speed} unattended={unattended}
            onReset={newConversation} onClose={() => setSettingsOpen(false)} />
        )}
      </div>

      <div className="body">
        <div className="main">
          <Progress run={run} finished={Boolean(outcome && outcome.kind === 'done')}
            partial={Boolean(outcome && outcome.kind === 'done' && outcome.open.length)} />
          {!run.connected && (
            <div className="banner" role="alert">No connection to the backend. Trying again…</div>
          )}
          {error && (
            <div className="banner banner-error" role="alert">
              <span>{error}</span>
              <button type="button" className="icon-btn icon-btn-small" aria-label="Dismiss" onClick={() => setError('')}><CloseIcon /></button>
            </div>
          )}
          <div className="scroll">
            <Thread run={run} outcome={outcome} history={history} debug={debug} elapsed={elapsed}
              onResume={resume} onAnswer={submit} />
            {!wide && run.task && (
              <button type="button" className="view-toggle" aria-expanded={viewOpen} onClick={() => setViewOpen(!viewOpen)}>
                <span className="view-thumb" aria-hidden="true"><i /><i /><i /></span>
                <span className="view-label">{viewOpen ? 'Hide the agent\'s view' : 'See the agent\'s view'}</span>
                <ChevronIcon open={viewOpen} />
              </button>
            )}
            {!wide && viewOpen && run.task && pane}
            {!wide && run.task && debugPanels}
          </div>
          <div className="foot">
            <KeyHint run={run} />
            <UnattendedNote run={run} on={unattended.on} onChange={() => { unattended.reload(); setSettingsOpen(true); }} />
            {run.phase === 'ended' && (
              <div className="foot-actions">
                <button type="button" className="link-btn" onClick={newConversation}>Start a new conversation</button>
              </div>
            )}
            <Composer run={run} outcome={outcome} onSubmit={submit} />
          </div>
        </div>
        {wide && (
          <aside className="side">
            {pane}
            {debugPanels}
          </aside>
        )}
      </div>
    </div>
  );
}
