import { Component, type ReactNode } from 'react';
import { ErrorState } from '../ui';

/** Contains a render crash (e.g. a malformed response) to one surface instead of blanking the app. Key it by surface so navigating resets it. */
export class SurfaceBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <ErrorState onRetry={() => window.location.reload()} retryLabel="Reload this page" title="This part of Lumina stopped working." />;
  }
}

/** Last line of defence around the whole app: a crash outside every surface (e.g. in the shell's
 *  own state) shows a way back instead of unmounting to a blank page. */
export class AppBoundary extends Component<{ children: ReactNode; onReload?: () => void }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="empty-state" role="alert">
        <h1>Lumina hit a problem</h1>
        <p>Something unexpected came back from the server. Your library and settings are safe.</p>
        <button className="g-button is-primary" onClick={() => (this.props.onReload ?? (() => window.location.reload()))()} type="button">Reload Lumina</button>
      </main>
    );
  }
}
