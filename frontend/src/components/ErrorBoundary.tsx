import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  crashed: boolean
}

// Last line of defence: a render error shows this instead of a blank page.
// React already logs the error to the console.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { crashed: false }

  static getDerivedStateFromError(): State {
    return { crashed: true }
  }

  render() {
    if (!this.state.crashed) return this.props.children
    return (
      <div className="crash" role="alert">
        <h1>Something went wrong</h1>
        <p>The page hit an unexpected error. Reload it and plan the trip again.</p>
        <button type="button" className="button" onClick={() => window.location.reload()}>
          Reload the page
        </button>
      </div>
    )
  }
}
