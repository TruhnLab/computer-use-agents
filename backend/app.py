#!/usr/bin/env python3
"""
Flask backend for Computer Use Agent
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import sys
import os
import io
import threading
import queue
import time
from contextlib import redirect_stdout, redirect_stderr

# Add parent directory to path to import agent
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from hospitalrun_agent import HospitalRunAgent

app = Flask(__name__)
CORS(app)

# Global queue for streaming logs
log_queue = queue.Queue()


class StreamCapture:
    """Capture stdout/stderr and send to queue"""
    def __init__(self, queue):
        self.queue = queue
        self.buffer = io.StringIO()

    def write(self, text):
        if text.strip():
            self.queue.put(text)
        sys.__stdout__.write(text)  # Also write to real stdout

    def flush(self):
        pass


def run_agent_task(task):
    """Run agent task in background thread"""
    try:
        log_queue.put(f"[SYSTEM] Initializing agent...\n")
        agent = HospitalRunAgent()

        log_queue.put(f"[SYSTEM] Starting task: {task}\n")

        # Capture stdout/stderr
        capture = StreamCapture(log_queue)

        with redirect_stdout(capture), redirect_stderr(capture):
            actions = agent.process_user_instruction(task)

        log_queue.put(f"[SYSTEM] Task completed with {len(actions)} actions\n")
        log_queue.put("[DONE]")
    except Exception as e:
        import traceback
        log_queue.put(f"[ERROR] {str(e)}\n")
        log_queue.put(f"[ERROR] {traceback.format_exc()}\n")
        log_queue.put("[DONE]")


@app.route('/api/task', methods=['POST'])
def start_task():
    """Start a new agent task"""
    try:
        data = request.json
        task = data.get('task', '')

        print(f"[API] Received task: {task}")

        if not task:
            return jsonify({'error': 'No task provided'}), 400

        # Clear the queue
        while not log_queue.empty():
            log_queue.get()

        # Start agent in background thread
        thread = threading.Thread(target=run_agent_task, args=(task,), daemon=True)
        thread.start()

        print(f"[API] Task started in background thread")
        return jsonify({'status': 'started', 'task': task})

    except Exception as e:
        print(f"[API] Error starting task: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/logs')
def stream_logs():
    """Stream logs using Server-Sent Events"""
    def generate():
        while True:
            try:
                # Wait for log message with timeout
                message = log_queue.get(timeout=30)

                if message == "[DONE]":
                    yield f"data: {message}\n\n"
                    break

                yield f"data: {message}\n\n"
            except queue.Empty:
                # Send keepalive
                yield ": keepalive\n\n"

    return Response(generate(), mimetype='text/event-stream')


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


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🤖 Computer Use Agent - Backend Started")
    print("="*60)
    print("\n✨ Open the web interface:")
    print("   👉 http://localhost:5000")
    print("\n" + "="*60 + "\n")
    app.run(debug=True, port=5000, threaded=True)
