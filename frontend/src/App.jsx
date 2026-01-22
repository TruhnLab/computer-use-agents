import { useState, useEffect, useRef } from 'react';
import './App.css';

function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [debugLogs, setDebugLogs] = useState([]);
  const [debugExpanded, setDebugExpanded] = useState(false);
  const messagesEndRef = useRef(null);
  const logsEndRef = useRef(null);

  const scrollToBottom = (ref) => {
    ref.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom(messagesEndRef);
  }, [messages]);

  useEffect(() => {
    if (debugExpanded) {
      scrollToBottom(logsEndRef);
    }
  }, [debugLogs, debugExpanded]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!input.trim() || isRunning) return;

    const userMessage = input.trim();
    setInput('');
    setMessages(prev => [...prev, { type: 'user', content: userMessage }]);
    setDebugLogs([]);
    setIsRunning(true);

    try {
      // Start task
      const response = await fetch('http://localhost:5000/api/task', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: userMessage })
      });

      if (!response.ok) throw new Error('Failed to start task');

      // Connect to log stream
      const eventSource = new EventSource('http://localhost:5000/api/logs');

      eventSource.onmessage = (event) => {
        const data = event.data;

        if (data === '[DONE]') {
          eventSource.close();
          setIsRunning(false);
          setMessages(prev => [...prev, {
            type: 'agent',
            content: 'Task completed! Check debug logs for details.'
          }]);
          return;
        }

        setDebugLogs(prev => [...prev, data]);
      };

      eventSource.onerror = () => {
        eventSource.close();
        setIsRunning(false);
        setMessages(prev => [...prev, {
          type: 'error',
          content: 'Connection error. Check if backend is running.'
        }]);
      };

    } catch (error) {
      setIsRunning(false);
      setMessages(prev => [...prev, {
        type: 'error',
        content: `Error: ${error.message}`
      }]);
    }
  };

  return (
    <div className="app">
      <div className="header">
        <h1>🤖 Computer Use Agent</h1>
        <p>AI-powered automation assistant</p>
      </div>

      <div className="main-container">
        {/* Chat Area */}
        <div className="chat-container">
          <div className="messages">
            {messages.length === 0 && (
              <div className="welcome">
                <h2>Welcome!</h2>
                <p>Enter a task below to get started.</p>
                <div className="examples">
                  <p>Example tasks:</p>
                  <ul>
                    <li>Create a new patient record</li>
                    <li>Fill out the registration form</li>
                    <li>Navigate to the dashboard</li>
                  </ul>
                </div>
              </div>
            )}

            {messages.map((msg, idx) => (
              <div key={idx} className={`message ${msg.type}`}>
                <div className="message-label">
                  {msg.type === 'user' ? '👤 You' :
                   msg.type === 'error' ? '❌ Error' : '🤖 Agent'}
                </div>
                <div className="message-content">{msg.content}</div>
              </div>
            ))}

            {isRunning && (
              <div className="message agent">
                <div className="message-label">🤖 Agent</div>
                <div className="message-content">
                  <div className="loading">
                    <span></span><span></span><span></span>
                  </div>
                  Working on it...
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          <form onSubmit={handleSubmit} className="input-form">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Enter a task for the agent..."
              disabled={isRunning}
              className="input-field"
            />
            <button type="submit" disabled={isRunning || !input.trim()} className="send-button">
              {isRunning ? '⏳' : '▶'}
            </button>
          </form>
        </div>

        {/* Debug Panel */}
        <div className={`debug-panel ${debugExpanded ? 'expanded' : ''}`}>
          <div className="debug-header" onClick={() => setDebugExpanded(!debugExpanded)}>
            <span>🔍 Debug Logs {debugLogs.length > 0 && `(${debugLogs.length})`}</span>
            <span className="toggle">{debugExpanded ? '▼' : '▲'}</span>
          </div>

          {debugExpanded && (
            <div className="debug-content">
              {debugLogs.length === 0 ? (
                <div className="debug-empty">No logs yet. Start a task to see debug output.</div>
              ) : (
                <pre className="debug-logs">
                  {debugLogs.map((log, idx) => (
                    <div key={idx} className="log-line">{log}</div>
                  ))}
                  <div ref={logsEndRef} />
                </pre>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
