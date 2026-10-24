import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Archivo with its width axis: the extended cut for headlines, normal width for everything else
import '@fontsource-variable/archivo/wdth.css'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
