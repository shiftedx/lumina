// ui first: the build emits the ui chunk CSS before app.css (an imported chunk CSS comes first), so dev cascades the same way.
import { ToastProvider } from './ui';
import './styles/app.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import LuminaApp from './LuminaApp';
import { AppBoundary } from './app/SurfaceBoundary';

const container = document.getElementById('root');

if (!container) {
  throw new Error('Root container not found');
}

createRoot(container).render(
  <StrictMode>
    <ToastProvider>
      <AppBoundary>
        <LuminaApp />
      </AppBoundary>
    </ToastProvider>
  </StrictMode>,
);
