#!/usr/bin/env python3
"""
Flask backend for Computer Use Agent
"""

import multiprocessing
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate

import requests
import urllib3
from dotenv import dotenv_values, load_dotenv
from flask import Flask, Response, jsonify, request
from flask_cors import CORS

# Add parent directory to path to import agent
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

_ENV_PATH = os.path.join(parent_dir, '.env')
load_dotenv(_ENV_PATH)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
# X-Frame-Id lets the UI tell two screenshots apart (see /api/screenshot).
CORS(app, expose_headers=['X-Frame-Id'])


class RunLog:
    """The log lines of the current run, kept in memory for /api/logs.

    A queue hands every line to exactly one reader: a page reload lost the run
    and a second tab stole lines from the first. Here every reader gets the
    whole run from the line it asks for, and a run that has ended can still be
    read until the next one starts."""

    def __init__(self):
        self._cond = threading.Condition()
        # Seeded with the clock, not 0: a reader that reconnects after a backend
        # restart must not find its old run id on a different run.
        self.run_id = int(time.time())
        self.lines = []
        self.task = None
        self.started = None
        self.done = True

    def start(self, task):
        """Begin a new run; returns its id (the writer passes it to put())."""
        with self._cond:
            self.run_id += 1
            self.lines, self.task, self.started, self.done = [], task, time.time(), False
            self._cond.notify_all()
            return self.run_id

    def clear(self):
        with self._cond:
            self.run_id += 1
            self.lines, self.task, self.started, self.done = [], None, None, True
            self._cond.notify_all()

    def put(self, msg, run_id=None):
        """Append a line. Lines for a run that has ended or been replaced are
        dropped, so a late writer cannot leak into the next run."""
        with self._cond:
            if self.done or (run_id is not None and run_id != self.run_id):
                return
            self.lines.append(msg.rstrip('\n'))
            if msg == '[DONE]':
                self.done = True
            self._cond.notify_all()

    def wait(self, run_id, index, timeout):
        """Lines of the current run from *index* on, waiting up to *timeout* for
        the first one. A reader that was following another run starts this one
        from its first line. Returns (run_id, index, lines, finished)."""
        with self._cond:
            if run_id != self.run_id:
                run_id, index = self.run_id, 0
            if index >= len(self.lines) and not (self.done and self.lines):
                self._cond.wait(timeout)
                if run_id != self.run_id:
                    run_id, index = self.run_id, 0
            batch = self.lines[index:]
            finished = bool(self.done and self.lines and index + len(batch) >= len(self.lines))
            return run_id, index, batch, finished

    def snapshot(self):
        with self._cond:
            return {'run_id': self.run_id, 'task': self.task, 'started': self.started,
                    'lines': len(self.lines), 'done': self.done}


run_log = RunLog()
# Global process handle for agent
agent_process = None
# Lock to prevent race conditions on agent_process
_agent_lock = threading.Lock()
# Model profiles selected in the UI (override the .env defaults for the next task).
# _selected_decider_profile == "SAME" means the planner's model also answers the
# per-step decision questions.
_selected_profile = None
_selected_decider_profile = None
# Runs that nobody attends (AUTONOMOUS in agent.py), as chosen in the UI for the
# next task. None = not chosen: the value of .env holds. Kept in memory only, so
# a restarted backend is back at what .env says and never unattended by surprise.
_selected_autonomous = None
# The mode the current (or last) run was started in, for the UI.
_run_autonomous = False


# --- Model profiles (from .env) ------------------------------------------- #

def _env_config():
    """Merged view of .env + process env (process env wins)."""
    cfg = dict(dotenv_values(_ENV_PATH))
    cfg.update(os.environ)
    return cfg


def _list_profiles(cfg=None):
    """Every profile with both a *_MODEL and a *_ENDPOINT in the config.

    Each profile carries two capability flags: 'vision' (from <PROFILE>_VISION,
    default true) and 'decision' (its endpoint is .../v1/systemone: a decision
    model, which answers typed questions and writes no text). A decision model
    can only be the DECIDER; the PLANNER must be a chat model."""
    cfg = cfg or _env_config()
    profiles = []
    for key, val in cfg.items():
        if key.endswith('_MODEL') and val:
            prof = key[:-len('_MODEL')]
            endpoint = cfg.get(f'{prof}_ENDPOINT')
            if prof and endpoint:
                vision_flag = str(cfg.get(f'{prof}_VISION', '1')).strip().lower()
                profiles.append({
                    'profile': prof,
                    'model': val,
                    'endpoint': endpoint,
                    'vision': vision_flag not in ('0', 'false', 'no', 'off'),
                    'decision': endpoint.rstrip('/').endswith('/systemone'),
                })
    profiles.sort(key=lambda p: p['profile'])
    return profiles


def _active_profile(cfg=None):
    cfg = cfg or _env_config()
    return (_selected_profile or cfg.get('ACTIVE_PROFILE') or '').upper()


def _active_decider_profile(cfg=None):
    """Decider profile: UI selection, else DECIDER_PROFILE from .env, else 'SAME'
    (the planner's model answers the decision questions too)."""
    cfg = cfg or _env_config()
    if _selected_decider_profile is not None:
        return _selected_decider_profile.upper()
    return (cfg.get('DECIDER_PROFILE') or '').strip().upper() or 'SAME'


def _endpoint_base(endpoint):
    """Strip a trailing /chat/completions or /systemone so we can query the
    sibling /models."""
    base = (endpoint or '').rstrip('/')
    for tail in ('/chat/completions', '/systemone'):
        if base.endswith(tail):
            return base[:-len(tail)]
    return base


def _live_models_for_base(base, api_key):
    """Set of model ids the server at *base* is actually serving (via GET /models),
    or an empty set if the endpoint is unreachable."""
    if not base:
        return set()
    try:
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        resp = requests.get(base + '/models', headers=headers, timeout=6, verify=False)
        resp.raise_for_status()
        return {m.get('id') for m in resp.json().get('data', [])}
    except Exception:
        return set()


def _availability(profiles, cfg):
    """Map each profile -> bool: is its model id actually being served? Groups by
    endpoint base so each proxy's /models is fetched once (concurrently)."""
    bases = {}  # base -> api_key (first profile that uses it)
    for p in profiles:
        base = _endpoint_base(p['endpoint'])
        if base and base not in bases:
            bases[base] = cfg.get(f"{p['profile']}_API_KEY", '')
    live_by_base = {}
    if bases:
        with ThreadPoolExecutor(max_workers=8) as ex:
            for base, live in ex.map(lambda b: (b, _live_models_for_base(b, bases[b])), list(bases)):
                live_by_base[base] = live
    return {
        p['profile']: p['model'] in live_by_base.get(_endpoint_base(p['endpoint']), set())
        for p in profiles
    }


def _temp_dir():
    return os.environ.get('TEMP', '/tmp')


def _pause_flag_path():
    """The agent is paused for as long as this file exists (agent.py reads it
    before every step and every input action; the pause key toggles it too)."""
    return os.path.join(_temp_dir(), 'agent_pause.flag')


def _stop_flag_path():
    """Writing this file asks the agent to stop, like its stop key. Written only
    while a run is active; the agent deletes it when it stops."""
    return os.path.join(_temp_dir(), 'agent_stop.flag')


# How long /api/stop waits for the agent to end the run itself before it kills
# the process (agreed with the harness: one screen read can take about 10 s).
_STOP_WAIT_SECONDS = 15


def _remove_flag(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"[API] Could not remove {path}: {exc}")


def _agent_alive():
    proc = agent_process
    return proc is not None and proc.is_alive()


def _clear_conversation(delete_screenshot=True):
    """Delete the persisted conversation context (and optionally the last
    screenshot). Used on startup (always fresh) and by /api/reset."""
    # The session also holds a question the last run ended on; a left-over pause
    # or stop flag would freeze or end the next run before its first step.
    paths = [os.path.join(_temp_dir(), 'agent_session.json'),
             os.path.join(_temp_dir(), 'agent_pause.flag'),
             os.path.join(_temp_dir(), 'agent_stop.flag')]
    if delete_screenshot:
        paths.append(os.path.join(_temp_dir(), 'agent_screenshot_debug.png'))
        paths.append(os.path.join(_temp_dir(), 'agent_screenshot_raw.png'))
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"[API] Could not remove {p}: {exc}")




def run_agent_task_proc(task, log_queue):
    """Run agent task in a subprocess, forwarding logs via a queue."""
    import logging
    import traceback

    from agent import ComputerAgent, _load_config

    # The new agent uses logging (not print), so redirect_stdout/stderr won't
    # capture it — StreamHandler stores the stream reference at creation time.
    # Attach a custom handler directly to the agent logger instead.
    class QueueLogHandler(logging.Handler):
        def __init__(self, q):
            super().__init__()
            self.q = q
        def emit(self, record):
            try:
                self.q.put(self.format(record) + "\n")
            except Exception:
                pass

    queue_handler = QueueLogHandler(log_queue)
    queue_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"
    ))
    agent_logger = logging.getLogger("agent")
    agent_logger.addHandler(queue_handler)
    agent_logger.propagate = False  # avoid double-logging to real stderr

    try:
        log_queue.put("[SYSTEM] Initializing agent...\n")
        agent = ComputerAgent(_load_config())
        log_queue.put(f"[SYSTEM] Starting task: {task}\n")
        result = agent.run(task)
        actions = result["actions"]
        # Check if the run ended with an abort
        aborted = actions and actions[-1].get("error", "").startswith("aborted")
        # A clarifying question ends the run and awaits the user's reply; it
        # takes precedence over any answer text.
        question = result.get("question")
        # Extract the final answer from the last verified task_complete action
        final_answer = None
        for action in reversed(actions):
            if action.get("action") == "task_complete" and action.get("verified"):
                final_answer = action.get("claim", "").strip()
                break
        log_queue.put(f"[SYSTEM] Task completed with {len(actions)} actions\n")
        if aborted:
            log_queue.put("[ABORTED]\n")
        elif question:
            log_queue.put(f"[QUESTION] {question}\n")
        elif final_answer:
            log_queue.put(f"[ANSWER] {final_answer}\n")
        log_queue.put("[DONE]")
    except Exception as e:
        log_queue.put(f"[ERROR] {str(e)}\n")
        log_queue.put(f"[ERROR] {traceback.format_exc()}\n")
        log_queue.put("[DONE]")


@app.route('/api/task', methods=['POST'])
def start_task():
    """Start a new agent task"""
    data = request.json
    task = data.get('task', '')

    print(f"[API] Received task: {task}")

    if not task:
        return jsonify({'error': 'No task provided'}), 400

    with _agent_lock:
        global agent_process
        if agent_process is not None and agent_process.is_alive():
            return jsonify({'error': 'Agent already running'}), 409

        # Delete the previous run's screenshots immediately, so the UI shows
        # "waiting" instead of a stale frame until the agent writes a fresh one.
        # (After the check above: a refused request must not take the running
        # agent's screenshots away.)
        for name in ('agent_screenshot_debug.png', 'agent_screenshot_raw.png'):
            try:
                os.remove(os.path.join(_temp_dir(), name))
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"[API] Could not remove stale screenshot: {exc}")

        # A new run replaces the previous run's lines in the log buffer.
        run_id = run_log.start(task)

        # Apply the UI-selected model profiles so the spawned agent picks them up
        # (the child inherits os.environ; load_dotenv won't override existing keys).
        if _selected_profile:
            os.environ['ACTIVE_PROFILE'] = _selected_profile
        if _selected_decider_profile is not None:
            # 'SAME' → the planner's model answers the decision questions too.
            os.environ['DECIDER_PROFILE'] = _selected_decider_profile
        # The same for the unattended mode; without a choice in the UI the
        # agent reads AUTONOMOUS from .env itself.
        global _run_autonomous
        if _selected_autonomous is not None:
            os.environ['AUTONOMOUS'] = '1' if _selected_autonomous else '0'
        _run_autonomous = _autonomous()

        # Use multiprocessing.Queue for inter-process log streaming
        mp_log_queue = multiprocessing.Queue()
        agent_process = multiprocessing.Process(
            target=run_agent_task_proc, args=(task, mp_log_queue), daemon=True)
        agent_process.start()

    def forward_logs():
        # NOTE: a quiet gap is NOT an error — a reasoning-model LLM call can
        # legitimately produce no log line for minutes. The forwarder must only
        # exit on [DONE] or when the agent process is actually gone; breaking
        # on queue.Empty (as this once did) silently froze the activity feed
        # for the rest of the run while the agent kept working.
        proc = agent_process
        while True:
            try:
                msg = mp_log_queue.get(timeout=2)
            except queue.Empty:
                if not proc.is_alive():
                    break
                continue
            except Exception:
                break
            run_log.put(msg, run_id)
            if msg == '[DONE]':
                return
        # The process is gone without a [DONE] (crashed, or killed from outside).
        # Close the run, or every reader would wait for it forever. A run that
        # /api/stop has closed already ignores both lines.
        run_log.put("[ERROR] The agent process ended unexpectedly.\n", run_id)
        run_log.put("[DONE]", run_id)

    threading.Thread(target=forward_logs, daemon=True).start()

    print("[API] Task started in subprocess")
    return jsonify({'status': 'started', 'task': task})

@app.route('/api/stop', methods=['POST'])
def stop_task():
    """Stop the running agent.

    The stop flag is the same request as the agent's stop key: the agent starts
    no further mouse or keyboard action, says where it was ([STOPPED]) and ends
    the run itself. What it cannot interrupt is one screen read (about 10 s) or
    one typing action, hence the wait. If it has not ended by then, or with
    {"force": true}, the process is killed, which is immediate but silent."""
    global agent_process
    force = bool((request.get_json(silent=True) or {}).get('force'))
    proc = agent_process
    if proc is not None and proc.is_alive():
        if not force:
            try:
                with open(_stop_flag_path(), 'w', encoding='utf-8') as fh:
                    fh.write('stop requested from the console')
                proc.join(timeout=_STOP_WAIT_SECONDS)
            except OSError as exc:
                print(f"[API] Could not write the stop flag: {exc}")
        killed = proc.is_alive()
        if killed:
            # Close the run first: the log forwarder closes a run whose process
            # has vanished as "ended unexpectedly", and this was not unexpected.
            run_log.put("[SYSTEM] Agent process terminated by user.\n")
            run_log.put("[DONE]")
            proc.terminate()
            proc.join(timeout=2)
        if agent_process is proc:
            agent_process = None
        # A killed agent cannot clean up after itself, and a run stopped while
        # paused must not leave the pause flag behind.
        _remove_flag(_stop_flag_path())
        _remove_flag(_pause_flag_path())
        return jsonify({'status': 'terminated' if killed else 'stopped'})
    else:
        # Ends a run whose process is gone without a [DONE]; a run that has
        # ended already ignores both lines.
        run_log.put("[SYSTEM] No agent process running.\n")
        run_log.put("[DONE]")
        _remove_flag(_stop_flag_path())
        _remove_flag(_pause_flag_path())
        return jsonify({'status': 'not_running'})


@app.route('/api/pause', methods=['POST'])
def pause_task():
    """Pause the running agent before its next step or input action. The agent
    answers with a [PAUSED] line; until then it may finish a model call."""
    if not _agent_alive():
        return jsonify({'error': 'No agent running'}), 409
    reason = ((request.json or {}).get('reason') or 'paused from the console').strip()
    try:
        with open(_pause_flag_path(), 'w', encoding='utf-8') as fh:
            fh.write(reason)
    except OSError as exc:
        return jsonify({'error': f'could not write the pause flag: {exc}'}), 500
    return jsonify({'status': 'pausing'})


@app.route('/api/resume', methods=['POST'])
def resume_task():
    """Resume a paused agent ([RESUMED]). It re-reads the screen and replans
    unless the pause fell between two steps and nothing changed."""
    _remove_flag(_pause_flag_path())
    return jsonify({'status': 'resumed'})


@app.route('/api/run')
def run_state():
    """What the UI needs to restore itself after a reload: is a run active,
    what was asked, how many lines /api/logs holds for it, and the two global
    keys of the agent (empty = disabled) so the UI can name them."""
    cfg = _env_config()
    state = run_log.snapshot()
    state.update({
        'active': _agent_alive(),
        'paused': os.path.exists(_pause_flag_path()),
        # Whether this run was started unattended (it neither asks nor pauses
        # by itself); the setting for the next run is /api/autonomous.
        'unattended': bool(state.get('task')) and _run_autonomous,
        'keys': {
            'stop': (cfg.get('STOP_KEY', 'pause') or '').strip().lower(),
            'pause': (cfg.get('PAUSE_KEY', 'scrolllock') or '').strip().lower(),
        },
    })
    return jsonify(state)


@app.route('/api/reset', methods=['POST'])
def reset_conversation():
    """Start a fresh conversation during usage: stop any running task and wipe
    the persisted context + last screenshot. (Startup is always fresh already.)"""
    global agent_process
    with _agent_lock:
        if agent_process is not None and agent_process.is_alive():
            agent_process.terminate()
            agent_process.join(timeout=2)
            agent_process = None
        _clear_conversation(delete_screenshot=True)
        run_log.clear()
    print("[API] Conversation reset — context and screenshot cleared.")
    return jsonify({'status': 'reset'})


@app.route('/api/screenshot')
def latest_screenshot():
    """Serve the latest screenshot with Last-Modified / 304 support: the
    annotated debug view by default, the unannotated frame with ?view=raw.
    Both are written by the agent at the same moment and show the same window."""
    import os

    from flask import send_file
    name = 'agent_screenshot_raw.png' if request.args.get('view') == 'raw' else 'agent_screenshot_debug.png'
    path = os.path.join(os.environ.get('TEMP', '/tmp'), name)
    try:
        stat = os.stat(path)
    except OSError:
        return jsonify({'error': 'No screenshot available yet'}), 404

    last_modified = formatdate(stat.st_mtime, usegmt=True)

    # Return 304 if the client already has the latest version.
    ims = request.headers.get('If-Modified-Since')
    if ims == last_modified:
        return '', 304

    resp = send_file(path, mimetype='image/png')
    resp.headers['Last-Modified'] = last_modified
    # Last-Modified has a resolution of one second; two frames can fall into
    # the same one. The UI compares this id to skip a frame it already shows.
    resp.headers['X-Frame-Id'] = str(stat.st_mtime_ns)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/api/logs')
def stream_logs():
    """Stream the current run's log lines using Server-Sent Events.

    Every event carries the id '<run>:<line>'. A reader without an id gets the
    run from its first line (this is how a reloaded page restores itself); a
    reader that reconnects sends Last-Event-ID and continues after that line.
    The stream ends after [DONE]. A reader whose run was replaced is moved to
    the new run's first line; it sees that in the run part of the id."""
    last = request.headers.get('Last-Event-ID') or request.args.get('after') or ''
    try:
        run_id, index = (int(part) for part in last.split(':'))
        index += 1
    except ValueError:
        run_id, index = None, 0

    def generate(run_id, index):
        while True:
            run_id, index, batch, finished = run_log.wait(run_id, index, timeout=30)
            if not batch and not finished:
                yield ": keepalive\n\n"
                continue
            for message in batch:
                # One 'data:' line per text line: a traceback has several, and
                # an unprefixed line would be dropped by the browser.
                body = "\n".join(f"data: {part}" for part in message.split('\n'))
                yield f"id: {run_id}:{index}\n{body}\n\n"
                index += 1
            if finished:
                break

    return Response(generate(run_id, index), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/')
def index():
    """Serve the frontend HTML"""
    frontend_path = os.path.join(parent_dir, 'frontend.html')
    try:
        with open(frontend_path, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return f'''
        <html>
            <head><title>Frontend Not Found</title></head>
            <body style="font-family: sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 2rem;">
                <h1>❌ Frontend not found</h1>
                <p>Could not find frontend.html at: {frontend_path}</p>
            </body>
        </html>
        ''', 404

@app.route('/api/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'})


@app.route('/api/info')
def info():
    """Return backend model info for the active profile."""
    cfg = _env_config()
    profile = _active_profile(cfg) or 'GEMMA'
    return jsonify({
        'model': cfg.get(f'{profile}_MODEL', 'unknown'),
        'endpoint': cfg.get(f'{profile}_ENDPOINT', 'unknown'),
        'profile': profile,
    })


@app.route('/api/models')
def list_models():
    """List all model profiles from .env, mark the active one, and probe which
    endpoints are currently reachable so the UI can grey out offline ones."""
    cfg = _env_config()
    profiles = _list_profiles(cfg)
    avail = _availability(profiles, cfg)  # model id must be served, not just host reachable
    models = [
        {'profile': p['profile'], 'model': p['model'],
         'available': avail.get(p['profile'], False), 'vision': p['vision'],
         'decision': p['decision']}
        for p in profiles
    ]
    planner, decider = _active_profile(cfg), _active_decider_profile(cfg)
    return jsonify({
        'models': models,
        'planner': planner,
        'decider': decider,       # 'SAME' = the planner's model decides too
        # Names from before the planner/decider split, kept until the UI has
        # moved to the two keys above.
        'active': planner,
        'nav_active': decider,
    })


@app.route('/api/select_model', methods=['POST'])
def select_model():
    """Select the profile used for the NEXT task. role='planner' (default) sets the
    planner model; role='decider' sets the model that answers the per-step
    decision questions ('SAME' = the planner's model). 'actioner' and 'navigator'
    are accepted as the former names of the two roles."""
    global _selected_profile, _selected_decider_profile
    data = request.json or {}
    role = (data.get('role') or 'planner').strip().lower()
    prof = (data.get('profile') or '').strip().upper()
    profiles = _list_profiles()
    known = {p['profile'].upper() for p in profiles}
    decision_by_prof = {p['profile'].upper(): p['decision'] for p in profiles}
    if role in ('decider', 'navigator'):
        # Any profile can decide: a decision model, or a chat model held to the options.
        if prof != 'SAME' and prof not in known:
            return jsonify({'error': f'unknown profile: {prof}'}), 400
        _selected_decider_profile = prof or 'SAME'
        print(f"[API] Decider profile selected: {_selected_decider_profile}")
        return jsonify({'status': 'ok', 'decider': _selected_decider_profile,
                        'nav_active': _selected_decider_profile})
    if prof not in known:
        return jsonify({'error': f'unknown profile: {prof}'}), 400
    if decision_by_prof.get(prof):
        # A decision model answers typed questions and writes no text, so it
        # cannot write the plan.
        return jsonify({'error': f'profile {prof} is a decision model — '
                                 'it can only be used as the decider'}), 400
    _selected_profile = prof
    print(f"[API] Planner profile selected: {prof}")
    return jsonify({'status': 'ok', 'planner': prof, 'active': prof})


# --- Cursor speed ---------------------------------------------------------- #
# How fast the agent's cursor travels to a click target: 1 = a deliberate crawl,
# 100 = near-instant. Floored at 1 rather than 0 because 0% would read as "the
# cursor doesn't move", which is never what it means. Kept in a file the agent
# re-reads on every glide rather than in env only, so dragging the slider takes
# effect during a running task and not just on the next one.

_SPEED_DEFAULT = 30.0
_SPEED_MIN = 1.0


def _cursor_speed_path():
    return os.path.join(_temp_dir(), 'agent_cursor_speed.txt')


def _cursor_speed_default():
    """Fall back to .env / process env, matching agent.py's own default."""
    try:
        return max(_SPEED_MIN, min(100.0, float(_env_config().get('MOUSE_GLIDE_SPEED_PCT', _SPEED_DEFAULT))))
    except (TypeError, ValueError):
        return _SPEED_DEFAULT


@app.route('/api/cursor_speed')
def get_cursor_speed():
    """Current cursor speed, 1 (slowest) - 100 (near-instant)."""
    try:
        with open(_cursor_speed_path(), encoding='utf-8') as fh:
            value = max(_SPEED_MIN, min(100.0, float(fh.read().strip())))
    except (OSError, ValueError):
        value = _cursor_speed_default()
    return jsonify({'speed': value, 'min': _SPEED_MIN})


@app.route('/api/cursor_speed', methods=['POST'])
def set_cursor_speed():
    data = request.json or {}
    try:
        value = float(data.get('speed'))
    except (TypeError, ValueError):
        return jsonify({'error': 'speed must be a number'}), 400
    value = max(_SPEED_MIN, min(100.0, value))
    try:
        with open(_cursor_speed_path(), 'w', encoding='utf-8') as fh:
            fh.write(f'{value:.1f}')
    except OSError as exc:
        return jsonify({'error': f'could not persist cursor speed: {exc}'}), 500
    # Also seed the env, so a task spawned before the file is read still agrees.
    os.environ['MOUSE_GLIDE_SPEED_PCT'] = f'{value:.1f}'
    print(f"[API] Cursor speed set to {value:.0f}%")
    return jsonify({'status': 'ok', 'speed': value})


# --- Unattended runs -------------------------------------------------------- #
# AUTONOMOUS=1: nobody attends the run, so the agent never asks and never pauses
# by itself (agent.py reads it once per run). The UI sets it for the next task
# the same way as the model profiles: through the environment the agent's
# process inherits.

def _autonomous(cfg=None):
    """The mode the next task starts in: the UI's choice, else .env, else off.
    Read like agent.py's _flag(), so both always agree."""
    if _selected_autonomous is not None:
        return _selected_autonomous
    cfg = cfg or _env_config()
    return (cfg.get('AUTONOMOUS') or '0').strip().lower() not in ('0', 'false', 'no', 'off')


@app.route('/api/autonomous')
def get_autonomous():
    return jsonify({'autonomous': _autonomous(), 'chosen': _selected_autonomous is not None})


@app.route('/api/autonomous', methods=['POST'])
def set_autonomous():
    """Choose the mode for the NEXT task; a running one keeps the mode it was
    started in."""
    global _selected_autonomous
    value = (request.json or {}).get('autonomous')
    if not isinstance(value, bool):
        return jsonify({'error': 'autonomous must be true or false'}), 400
    _selected_autonomous = value
    print(f"[API] Unattended runs: {'on' if value else 'off'}")
    return jsonify({'status': 'ok', 'autonomous': value})


if __name__ == '__main__':
    # Startup is always a fresh conversation.
    _clear_conversation(delete_screenshot=True)
    print("\n" + "="*60)
    print("🤖 Computer Use Agent - Backend Started")
    print("="*60)
    print("\n✨ Open the web interface:")
    print("   👉 http://localhost:3000")
    print("\n" + "="*60 + "\n")
    app.run(debug=False, port=5000, threaded=True)
