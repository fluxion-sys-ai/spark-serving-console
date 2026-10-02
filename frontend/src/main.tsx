import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './upstream.css';
import './console.css';
import { getThemeChoice, setTheme } from './theme';

setTheme(getThemeChoice());

async function authenticateFromFragment() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get('access_token');
  if (!token) return;
  try {
    const response = await fetch('/api/session', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token}),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Authentication failed (${response.status})`);
    }
  } finally {
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }
}

const root = ReactDOM.createRoot(document.getElementById('root')!);
authenticateFromFragment()
  .then(() => root.render(<React.StrictMode><App /></React.StrictMode>))
  .catch(error => root.render(<div className="auth-error"><h1>Console access failed</h1><p>{String(error)}</p><p>Run <code>./console.sh status</code> on the Spark and open the authenticated URL it prints.</p></div>));
