// Calls to the Flask backend (backend/app.py).

export const API = import.meta.env.VITE_API_BASE || 'http://localhost:5000';

async function request(path, options) {
  const res = await fetch(`${API}${path}`, options);
  let body = null;
  try { body = await res.json(); } catch (_) { /* no JSON body */ }
  if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
  return body;
}

const post = (path, data) => request(path, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(data || {}),
});

export const api = {
  run: () => request('/api/run'),
  startTask: (task) => post('/api/task', { task }),
  // Asks the agent to stop itself; `force` kills the process at once.
  stop: (force = false) => post('/api/stop', { force }),
  pause: () => post('/api/pause', {}),
  resume: () => post('/api/resume', {}),
  reset: () => post('/api/reset', {}),
  models: () => request('/api/models'),
  selectModel: (profile, role) => post('/api/select_model', { profile, role }),
  cursorSpeed: () => request('/api/cursor_speed'),
  setCursorSpeed: (speed) => post('/api/cursor_speed', { speed }),
  // Unattended runs: the agent neither asks nor pauses by itself. For the next request.
  autonomous: () => request('/api/autonomous'),
  setAutonomous: (on) => post('/api/autonomous', { autonomous: on }),
};

export const logsUrl = () => `${API}/api/logs`;
export const screenshotUrl = (view) => `${API}/api/screenshot?view=${view}&t=${Date.now()}`;
