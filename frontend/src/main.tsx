import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { SpeechProvider } from './SpeechProvider';
import './styles.css';
import './visualPolish.css';
import './componentPolish.css';
import './studioExperience.css';
import './focusReading.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><SpeechProvider><App /></SpeechProvider></React.StrictMode>,
);
