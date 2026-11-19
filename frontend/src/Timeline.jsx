import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  actionText, awaitsCheck, buildTimeline, decisionPhrase, describeAction, failedLate, formatDuration,
  lastRound, leadSentence, pauseSentence, roundTitle, secondsBetween, splitLead, splitPoint, visibleSteps,
} from './runModel';
import { CheckIcon, ChevronIcon, CrossIcon, PauseIcon, PlayIcon, ReplanIcon, SpinnerIcon, StopIcon } from './icons';

// The request, the plan as a timeline of steps, and how the run ended.

const NODE_LABEL = {
  done: 'Verified', typed: 'Typed, not checked yet', failed: 'Failed', running: 'Running', paused: 'Paused', pending: 'Not started',
  skipped: 'Dropped', interrupted: 'Not finished', replan: 'Plan changed', planning: 'Planning',
  question: 'Question', stopped: 'Stopped', problem: 'Problem', result: 'Done',
  next: 'Next', aside: 'Set aside', partial: 'Partly done', own: 'Decided without asking', unanswered: 'Ended on a question',
};

function Node({ kind }) {
  let content = null;
  if (kind === 'done' || kind === 'result' || kind === 'typed') content = <CheckIcon />;
  else if (kind === 'failed' || kind === 'problem') content = <CrossIcon />;
  else if (kind === 'running' || kind === 'planning' || kind === 'next') content = <SpinnerIcon />;
  else if (kind === 'aside' || kind === 'partial' || kind === 'unanswered') content = <span aria-hidden="true">!</span>;
  else if (kind === 'own') content = <span aria-hidden="true">?</span>;
  else if (kind === 'paused') content = <PauseIcon size={10} />;
  else if (kind === 'replan') content = <ReplanIcon />;
  else if (kind === 'question') content = <span aria-hidden="true">?</span>;
  else if (kind === 'stopped') content = <StopIcon size={9} />;
  return <span className={`node node-${kind}`} role="img" aria-label={NODE_LABEL[kind] || kind}>{content}</span>;
}

function Row({ node, last, current, children }) {
  return (
    <div className="row" data-current={current ? 'true' : undefined}>
      <div className="rail">
        <Node kind={node} />
        {!last && <span className={`rail-line${node === 'done' ? ' passed' : ''}`} />}
      </div>
      <div className="row-body">{children}</div>
    </div>
  );
}

const Chip = ({ tone, children }) => <span className={`chip${tone ? ` chip-${tone}` : ''}`}>{children}</span>;

const COMMITS_LABEL = 'Saves or sends data';

function decisionText(ev) {
  const parts = [`${ev.question}: ${ev.label != null ? ev.label : (ev.choice != null ? ev.choice : 'no answer')}`];
  if (ev.p != null) parts.push(`p ${Number(ev.p).toFixed(2)}`);
  if (ev.seconds != null) parts.push(`${ev.seconds} s`);
  if (ev.accepted != null) parts.push(ev.accepted ? 'accepted' : 'not accepted');
  if (ev.image) parts.push('with image');
  return parts.join(' · ');
}

function EventList({ events }) {
  if (!events.length) return null;
  return (
    <div className="events">
      {events.map((ev, i) => {
        let tag = ev.type;
        let text = ev.text;
        if (ev.type === 'action') text = actionText(ev.action, ev.args);
        else if (ev.type === 'decision') { tag = 'decider'; text = decisionText(ev); }
        return (
          <div className={`event event-${ev.type}`} key={i}>
            <span className="event-tag">{tag}</span>
            <span className="event-text">{text}</span>
          </div>
        );
      })}
    </div>
  );
}

function PausedBlock({ title, run, onResume }) {
  return (
    <div className="block block-paused">
      {title && <div className="block-title">{title}</div>}
      <div className="block-text">{pauseSentence(run.pauseReason)} The mouse and keyboard are yours.</div>
      <button type="button" className="btn btn-primary btn-wide" onClick={onResume} disabled={run.intent === 'resuming'}>
        <PlayIcon size={13} />
        <span>{run.intent === 'resuming' ? 'Resuming…' : 'Resume'}</span>
      </button>
      <div className="block-hint">The agent will look at the screen again and adjust its plan.</div>
    </div>
  );
}

function stepMeta(todo, decisions) {
  const parts = [];
  if (todo.resolved_by === 'decider') {
    const accepted = decisions.filter((d) => d.accepted && d.p != null).pop();
    parts.push(accepted ? `decider ${Number(accepted.p).toFixed(2)}` : 'decider');
  } else if (todo.resolved_by === 'workflow') {
    parts.push('stored workflow');
  } else if (todo.resolved_by) {
    parts.push(todo.resolved_by);
  }
  if (todo.seconds != null) parts.push(formatDuration(todo.seconds));
  return parts.join(' · ');
}

// A sentence as the operator reads it: capital first, full stop last.
const sentence = (text) => {
  const t = String(text || '').trim();
  return t ? `${t.charAt(0).toUpperCase()}${t.slice(1).replace(/\.$/, '')}.` : '';
};

function StepRow({ todo, step, run, last, debug, open, next, onToggle, onResume }) {
  const ended = run.phase === 'ended';
  const active = todo.status === 'in_progress';
  // "typed": entered, but the value has not been read back from the screen yet.
  // It gets its own look, so that a check mark always means "seen on screen".
  const typed = awaitsCheck(todo);
  // "next": between two steps the agent looks at the screen for the step that
  // comes now. That step shows it, in its place in the list.
  const kind = active
    ? (ended ? 'interrupted' : run.paused ? 'paused' : 'running')
    : typed ? 'typed' : (next && todo.status === 'pending') ? 'next' : todo.status;
  // Which of several equal controls a click went to: the other texts of its row.
  const where = todo.row ? `In the row: ${String(todo.row).split(/\s*\|\s*/).join(' · ')}` : '';
  // Decisions made during the step. The ones that led to it (the decider
  // choosing this click) are in the same list but carry no step id.
  const decisions = step.events.filter((e) => e.type === 'decision' && e.step === todo.id);
  const lastDecision = decisions[decisions.length - 1];
  const unsure = decisions.filter((d) => d.accepted === false).pop();
  const chosenByModel = todo.planned_by === 'decider';
  const meta = debug ? stepMeta(todo, decisions) : '';
  const hasDetails = debug && step.events.length > 0;

  let body;
  if (kind === 'paused') {
    body = <PausedBlock title={todo.text} run={run} onResume={onResume} />;
  } else if (kind === 'running') {
    let phase = todo.op === 'check' ? 'Checking the screen' : 'Looking at the screen';
    if (run.intent === 'stopping') phase = 'Stopping';
    else if (run.intent === 'pausing') phase = 'Pausing before the next action';
    else if (run.retry) phase = 'The model did not answer. Trying again.';
    else if (run.openAction && run.openAction.step === todo.id) phase = describeAction(run.openAction.action, run.openAction.args);
    else if (step.events.some((e) => e.type === 'result')) phase = 'Checking the result';
    body = (
      <div className="block block-now">
        <div className="block-title">{todo.text}</div>
        <div className="block-text">{phase}</div>
        {where && <div className="block-hint">{where}</div>}
        {(lastDecision || chosenByModel || todo.commits) && (
          <div className="chips">
            {lastDecision ? (
              <Chip>{lastDecision.accepted === false ? 'The model was unsure' : `Asked the model ${decisionPhrase(lastDecision.question)}`}</Chip>
            ) : chosenByModel && (
              <Chip>The model chose this click</Chip>
            )}
            {todo.commits && <Chip tone="warn">{COMMITS_LABEL}</Chip>}
          </div>
        )}
      </div>
    );
  } else {
    let note = todo.note;
    // A failure note often goes on with advice for the planner: the operator
    // reads what went wrong, Debug the whole note.
    if (kind === 'failed') note = `Failed: ${(debug ? todo.note : leadSentence(todo.note)) || 'no reason given'}`;
    else if (kind === 'typed' && !debug) note = run.phase === 'ended' ? 'Typed. Not checked on screen.' : 'Typed, not checked yet.';
    else if (kind === 'interrupted') note = 'Not finished: the run ended during this step.';
    else if (kind === 'next') note = run.retry ? 'The model did not answer. Trying again.' : '';
    else if (kind === 'pending') note = '';
    const upcoming = kind === 'pending' || kind === 'next';
    const showCommit = todo.commits && kind !== 'skipped' && (upcoming || debug);
    body = (
      <div className={`step step-${kind}`}>
        <div className="step-text">{todo.text}</div>
        {note && <div className="step-note">{note}</div>}
        {where && !upcoming && kind !== 'skipped' && <div className="step-note step-where">{where}</div>}
        {/* The note is a sentence for the operator; the change counts behind it are for Debug. */}
        {debug && todo.detail && <div className="step-note step-detail">{todo.detail}</div>}
        {(showCommit || (debug && (unsure || chosenByModel))) && (
          <div className="chips">
            {showCommit && <Chip tone="warn">{COMMITS_LABEL}</Chip>}
            {debug && chosenByModel && <Chip>Chosen by the decider</Chip>}
            {debug && unsure && (
              <Chip>Decider unsure{unsure.p != null ? ` (${Number(unsure.p).toFixed(2)})` : ''}</Chip>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <Row node={kind} last={last} current={(active && !ended) || kind === 'next'}>
      {debug ? (
        <>
          <div className="row-head">
            <div className="row-main">{body}</div>
            {meta && <span className={`meta${todo.resolved_by === 'decider' ? ' meta-model' : ''}`}>{meta}</span>}
            {hasDetails && (
              <button type="button" className="icon-btn icon-btn-small" onClick={onToggle}
                aria-expanded={open} aria-label={open ? 'Hide details' : 'Show details'}>
                <ChevronIcon open={open} />
              </button>
            )}
          </div>
          {hasDetails && open && <EventList events={step.events} />}
        </>
      ) : body}
    </Row>
  );
}

function MarkRow({ mark, run, last, debug, active }) {
  const replan = mark.type === 'replan';
  // A step that failed only when its value was read back sits further up in
  // the list, possibly out of view: the break names it.
  const late = !active && failedLate(mark, run.todos);
  const lateStep = late ? run.todos.find((t) => t.id === mark.stepId) : null;
  const title = lateStep ? `An earlier step did not hold: ${lateStep.text}` : roundTitle(mark, active);
  const took = secondsBetween(mark.startTs, mark.endTs);
  return (
    <Row node={active ? 'planning' : mark.type === 'plan' ? 'done' : 'replan'} last={last} current={active}>
      <div className="row-head">
        <div className="row-main">
          <div className="mark-title">{title}</div>
          {active && run.retry && <div className="step-note">The model did not answer. Trying again.</div>}
          {debug && replan && mark.detail && <div className="step-note">{mark.detail}</div>}
          {debug && mark.assessment && <div className="step-note">Planner: {mark.assessment}</div>}
        </div>
        {debug && took != null && <span className="meta">planner · {formatDuration(took)}</span>}
      </div>
      {debug && (
        <EventList events={[...mark.supervise.map((text) => ({ type: 'reason', text })), ...mark.events]} />
      )}
    </Row>
  );
}

// A step the agent could not carry out and left for the operator, so that the
// rest of the request could go on. Shown where it happened; the result names
// it again.
function AsideRow({ mark, last, debug }) {
  // Unattended: a question of the planner that nobody could be asked. The
  // planner settled it itself; a person will want to see which one it was.
  if (mark.kind === 'own') {
    return (
      <Row node="own" last={last}>
        <div className="step step-aside">
          <div className="step-text">Decided without asking</div>
          <div className="step-note">The agent would have asked: “{mark.text}” Nobody attends this run, so it decided itself.</div>
        </div>
      </Row>
    );
  }
  const [what, why] = splitPoint(mark.text);
  return (
    <Row node="aside" last={last}>
      <div className="step step-aside">
        <div className="step-text">Set aside: {what}</div>
        <div className="step-note">
          {why ? `${sentence(debug ? why : leadSentence(why))} ` : ''}The agent goes on without it.
        </div>
      </div>
    </Row>
  );
}

function QuestionBlock({ outcome, onAnswer, disabled }) {
  // A question can be long (when the agent is stuck it says what it tried):
  // its first sentence is the heading, the rest is read as text.
  const [lead, rest] = splitLead(outcome.text);
  return (
    <div className="block block-question">
      <div className="block-title">{lead}</div>
      {rest && <div className="block-body">{rest}</div>}
      {outcome.options.length > 0 && (
        <div className="options">
          {outcome.options.map((option) => (
            <button type="button" className="btn btn-option" key={option} disabled={disabled} onClick={() => onAnswer(option)}>
              {option}
            </button>
          ))}
        </div>
      )}
      <div className="block-hint">
        {outcome.options.length > 0 ? 'Or type a different answer below.' : 'Type your answer below.'}
      </div>
    </div>
  );
}

function endMessage(run, outcome) {
  if (outcome.kind === 'stopped') {
    const running = run.todos.find((t) => t.status === 'in_progress');
    const text = (run.stopped && run.stopped.text) || (running && running.text) || '';
    const cutOff = run.openAction && /type/.test(run.openAction.action);
    return {
      node: 'stopped',
      title: 'Stopped',
      text: text ? `Stopped during “${text}”. This step was not completed.` : 'Stopped between two steps.',
      hint: cutOff ? 'A field may be half typed. Please check it on the screen.' : '',
    };
  }
  if (outcome.kind === 'unanswered') {
    return {
      node: 'unanswered', title: 'Ended on a question',
      text: `The agent would have asked: “${outcome.text}”`,
      hint: 'Nobody attends this run, so it ended here instead of asking. Please check on the screen what was done so far.',
    };
  }
  if (outcome.kind === 'aborted') {
    return {
      node: 'problem', title: 'Could not finish',
      text: outcome.text || 'The agent gave up on this request.',
      hint: 'Please check on the screen what was done so far.',
    };
  }
  if (outcome.kind === 'error') {
    return {
      node: 'problem', title: 'Something went wrong', text: outcome.text,
      hint: 'The full message is in the raw log (Debug mode).',
    };
  }
  return {
    node: 'problem', title: 'Ended without a checked result',
    text: 'The agent stopped without confirming the result.',
    hint: 'Please check on the screen what was done.',
  };
}

function Timeline({ run, outcome, debug, elapsed, onResume, onAnswer }) {
  const [openSteps, setOpenSteps] = useState({});
  const items = buildTimeline(run, debug);
  const running = run.phase === 'running';
  const lastMark = lastRound(run.marks);
  const rows = [];
  // Between two steps no step is running. What happens then (the agent looks
  // at the screen, or is paused) is shown at the step that comes next, not
  // below the steps still planned: a long plan would push it out of view.
  const idle = running && run.currentStepId == null;
  // A step is announced ([STEP]) a moment before the plan says it is running:
  // for that moment it is the step that comes next, too.
  const announced = running && run.currentStepId != null
    ? run.todos.find((t) => t.id === run.currentStepId && t.status === 'pending') : null;
  const upNext = announced || (idle ? run.todos.find((t) => t.status === 'pending') : null);
  let placed = false;
  // Debug: what happened since the last step and belongs to none (the decider
  // choosing the next click, the completion check). Shown in the last row.
  const loose = debug && run.loose.length > 0 ? <EventList events={run.loose} /> : null;

  for (const item of items) {
    if (item.kind === 'step') {
      const { todo } = item;
      const step = run.steps[todo.id] || { events: [] };
      const open = openSteps[todo.id] != null ? openSteps[todo.id] : todo.id === run.currentStepId;
      let next = false;
      if (todo === upNext && run.paused && idle) {
        placed = true;
        rows.push((last) => (
          <Row key="paused" node="paused" last={last} current>
            <PausedBlock run={run} onResume={onResume} />
          </Row>
        ));
      } else if (todo === upNext && (announced || !run.planning)) {
        placed = true;
        next = true;
      }
      // Keyed by `key`, not by id: a step still to come keeps its row when the
      // planner puts others in front of it and it gets a new number.
      rows.push((last) => (
        <StepRow key={`s${todo.key != null ? todo.key : todo.id}`} todo={todo} step={step} run={run} last={last} debug={debug}
          open={open} next={next} onToggle={() => setOpenSteps((o) => ({ ...o, [todo.id]: !open }))} onResume={onResume} />
      ));
    } else if (item.mark.type === 'aside') {
      const { mark } = item;
      rows.push((last) => <AsideRow key={`m${mark.id}`} mark={mark} last={last} debug={debug} />);
    } else {
      const { mark } = item;
      const active = running && run.planning && mark === lastMark;
      // The operator sees whatever is happening now, and a break that changed
      // the plan. A failed step that dropped nothing already says so itself.
      // (Unless it failed late: that step is further up, so the break names it.)
      const changedPlan = mark.type === 'replan'
        && (failedLate(mark, run.todos) || !(mark.reason === 'failed_step' && !(mark.dropped || []).length));
      if (!debug && !active && !changedPlan) continue;
      rows.push((last) => <MarkRow key={`m${mark.id}`} mark={mark} run={run} last={last} debug={debug} active={active} />);
    }
  }

  if (idle && !placed) {
    if (run.paused) {
      rows.push((last) => (
        <Row key="paused" node="paused" last={last} current>
          <PausedBlock run={run} onResume={onResume} />
        </Row>
      ));
    } else if (!run.planning) {
      const text = run.intent === 'stopping' ? 'Stopping'
        : run.todos.length ? 'Looking at the screen' : 'Getting ready';
      rows.push((last) => (
        <Row key="busy" node="planning" last={last} current>
          <div className="mark-title">{text}</div>
          {run.retry && <div className="step-note">The model did not answer. Trying again.</div>}
          {loose}
        </Row>
      ));
    }
  }

  if (outcome && outcome.kind === 'done') {
    const count = visibleSteps(run).length;
    const facts = [count ? `${count} ${count === 1 ? 'step' : 'steps'}` : '', formatDuration(elapsed)].filter(Boolean);
    // A request that ended with something set aside is not "Done": it gets its
    // own colour and says what is left for the operator.
    const partial = outcome.open.length > 0;
    rows.push((last) => (
      <Row key="result" node={partial ? 'partial' : 'result'} last={last} current>
        <div className={`block block-result${partial ? ' block-partial' : ''}`}>
          <div className="result-label">{partial ? 'Partly done' : 'Done'}</div>
          <div className="block-title">{outcome.text}</div>
          {partial && (
            <div className="open-points">
              <div className="open-head">Not done</div>
              <ul>
                {outcome.open.map((point, i) => {
                  const [what, why] = splitPoint(point);
                  return (
                    <li key={i}>
                      <strong>{what}</strong>
                      {why && <span>{sentence(debug ? why : leadSentence(why))}</span>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <div className="block-text">
            {run.evidence.length ? `Found on screen: ${run.evidence.join(' · ')}`
              : run.evidenceNote ? `${run.evidenceNote.charAt(0).toUpperCase()}${run.evidenceNote.slice(1).replace(/\.$/, '')}.`
                : 'Checked on screen.'}
          </div>
          {facts.length > 0 && <div className="block-hint">{facts.join(' · ')}</div>}
        </div>
        {loose}
      </Row>
    ));
  } else if (outcome && outcome.kind === 'question') {
    rows.push((last) => (
      <Row key="question" node="question" last={last} current>
        <QuestionBlock outcome={outcome} onAnswer={onAnswer} />
        {loose}
      </Row>
    ));
  } else if (outcome && outcome.kind !== 'done') {
    const end = endMessage(run, outcome);
    rows.push((last) => (
      <Row key="end" node={end.node} last={last} current>
        <div className={`block block-${end.node}`}>
          <div className="block-title">{end.title}</div>
          {end.text && <div className="block-text">{end.text}</div>}
          {end.hint && <div className="block-hint">{end.hint}</div>}
        </div>
        {loose}
      </Row>
    ));
  }

  return <div className="timeline">{rows.map((render, i) => render(i === rows.length - 1))}</div>;
}

function pastReply(outcome) {
  if (!outcome) return { node: 'stopped', text: 'Not finished' };
  switch (outcome.kind) {
    case 'done': return (outcome.open || []).length
      ? { node: 'aside', text: `${outcome.text} Not done: ${outcome.open.map((p) => splitPoint(p)[0]).join('; ')}.` }
      : { node: 'done', text: outcome.text };
    case 'question': return { node: 'question', text: outcome.text };
    case 'stopped': return { node: 'stopped', text: 'Stopped' };
    case 'unanswered': return { node: 'aside', text: `Ended on a question: ${outcome.text}` };
    case 'aborted': return { node: 'problem', text: 'Could not finish' };
    case 'error': return { node: 'problem', text: 'Something went wrong' };
    default: return { node: 'problem', text: 'Ended without a checked result' };
  }
}

const Bubble = ({ children }) => (
  <div className="bubble-line"><div className="bubble">{children}</div></div>
);

export function Thread({ run, outcome, history, debug, elapsed, onResume, onAnswer }) {
  const end = useRef(null);
  const root = useRef(null);
  const follow = useRef(true);
  const [following, setFollowing] = useState(true);
  const hasTask = Boolean(run.task);

  // Where the panel should stand (`to`), where it stands (`at`), and where the
  // row that is happening now begins (`top`), all as scroll positions.
  //
  // It stands at the end of the thread, with one exception: a long plan has
  // many steps still to come below the running one, and showing the end would
  // push the running step out of view. Then the running row is kept in view,
  // a little below the top, with as much of what follows as fits. A row taller
  // than the panel (a step with its details in Debug) is shown by its end,
  // where the new lines arrive.
  const aim = useCallback(() => {
    const scope = root.current;
    const box = scope && scope.closest('.scroll');
    if (!box || !end.current) return null;
    const view = box.getBoundingClientRect();
    const at = box.scrollTop;
    const toEnd = at + end.current.getBoundingClientRect().bottom - view.bottom;
    const rows = scope.querySelectorAll('[data-current="true"]');
    const now = rows[rows.length - 1];
    if (!now) return { box, at, to: Math.max(0, toEnd), top: Infinity };
    const row = now.getBoundingClientRect();
    const top = at + row.top - view.top;
    const bottom = top + row.height;
    const endAt = toEnd + view.height;
    // The end wins whenever it fits into the panel together with the running
    // row, and when that row is the last one.
    if (endAt - top <= view.height || endAt - bottom <= 48) return { box, at, to: Math.max(0, toEnd), top };
    const lead = Math.min(120, view.height / 4);
    return { box, at, to: Math.max(0, top - lead, bottom + 16 - view.height), top };
  }, []);

  // Moves the panel there at once: an animated scroll would lag behind a step
  // that keeps growing, and would be mistaken for the user scrolling. It moves
  // down whenever there is more to show, and up only when the running row
  // would otherwise begin above the visible part: a row that merely got
  // shorter does not make the list jump.
  const settle = useCallback((exactly) => {
    const a = aim();
    if (!a) return;
    const off = exactly
      ? Math.abs(a.at - a.to) > 0.5
      : a.to - a.at > 0.5 || a.at - Math.max(a.to, a.top) > 0.5;
    if (off) a.box.scrollTop = Math.ceil(a.to);
  }, [aim]);

  const setFollow = useCallback((on) => {
    follow.current = on;
    setFollowing(on);
  }, []);

  // The panel follows whenever the thread changes, by whatever cause: a new
  // step, a step's details filling in (in Debug a step gains several lines
  // while it runs), an answer, a row opened by hand. The one exception is a
  // user who has scrolled away to read, up or down: then it stays put until
  // they return or press "Latest".
  useEffect(() => {
    const scope = root.current;
    const box = scope && scope.closest('.scroll');
    if (!scope || !box) return undefined;
    const onScroll = () => {
      const a = aim();
      if (!a) return;
      const near = a.at >= a.to - 48 && a.at <= Math.max(a.to, a.top) + 48;
      if (near !== follow.current) setFollow(near);
    };
    const grown = new ResizeObserver(() => { if (follow.current) settle(false); });
    grown.observe(scope);
    grown.observe(box);
    box.addEventListener('scroll', onScroll, { passive: true });
    return () => { grown.disconnect(); box.removeEventListener('scroll', onScroll); };
  }, [hasTask, aim, settle, setFollow]);

  // Every time the thread is drawn again with new content, the position is
  // corrected before the browser paints, so no frame shows the running step
  // or the end cut off. (The observer above covers growth that does not come
  // through here, such as a row the user opens.)
  useLayoutEffect(() => {
    if (follow.current) settle(false);
  });

  // What the operator must not miss brings the panel back even if they had
  // scrolled away: a new request, a pause, and how the run ended.
  const mustSee = [run.runId, run.phase, run.paused, outcome && outcome.kind].join('|');
  useLayoutEffect(() => {
    setFollow(true);
    settle(true);
  }, [mustSee, setFollow, settle]);

  if (!run.task) {
    return (
      <div className="thread" ref={root}>
        <div className="welcome">
          <h1>What should I do?</h1>
          <p>Describe the task in plain language. I work in the application on your screen, and you can pause or stop me at any time.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="thread" ref={root}>
      {history.map((past, i) => {
        const reply = pastReply(past.outcome);
        return (
          <div className="past" key={`${past.runId}-${i}`}>
            <Bubble>{past.task}</Bubble>
            <div className="past-reply">
              <Node kind={reply.node} />
              <span>{reply.text}</span>
            </div>
          </div>
        );
      })}
      <Bubble>{run.task}</Bubble>
      {/* The steps stay where they were when a run ends, and the result is
          added below them: nothing the operator was reading moves away. */}
      <section className="card">
        <Timeline run={run} outcome={outcome} debug={debug} elapsed={elapsed} onResume={onResume} onAnswer={onAnswer} />
      </section>
      {!following && (
        <div className="latest">
          <button type="button" onClick={() => { setFollow(true); settle(true); }}>
            <ChevronIcon />
            <span>Latest</span>
          </button>
        </div>
      )}
      <div ref={end} />
    </div>
  );
}
