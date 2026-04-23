import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';

// Variable font files — one per axis range. Vite bundles them into dist/assets/.
import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';

import './index.css';

document.documentElement.classList.add('dark');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
