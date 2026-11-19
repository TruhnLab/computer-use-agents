import React from 'react'
import ReactDOM from 'react-dom/client'
// The typeface is bundled with the app: the console must run without internet.
import '@fontsource-variable/figtree'
import './styles.css'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
