import { useEffect, useState } from 'react';
import { LoaderCircle, RotateCcw, Sparkles } from 'lucide-react';
import { ApiRequestError, getIdeaGraph, getLatestIdeaGraph, type IdeaGraph, requestIdeaGraph, type Summary } from '../../api';
import { cueTime } from './TranscriptPanel';
import { usePollJob } from './usePollJob';

const DONE = new Set(['succeeded', 'failed', 'interrupted']);

/**
 * Radial positions in percent of a square box; index order is stable so the layout never shuffles.
 * Past 12 ideas every other node moves to an inner ring so neighbours keep a 44px target apart on phones.
 */
export function radialLayout(count: number): Array<{ x: number; y: number }> {
  return Array.from({ length: count }, (_, index) => {
    const angle = (2 * Math.PI * index) / Math.max(1, count) - Math.PI / 2;
    const radius = count > 12 && index % 2 ? 27 : 38;
    return count === 1 ? { x: 50, y: 50 } : { x: 50 + radius * Math.cos(angle), y: 50 + radius * Math.sin(angle) };
  });
}

/**
 * Idea graph tool: the list is the primary, keyboard-complete view; the map is the same data
 * drawn as a simple radial diagram (plain buttons over an inline SVG, no graph library).
 */
export function IdeaGraphPanel({ itemId, summary, onSeek }: { itemId: string; summary: Summary; onSeek: (seconds: number) => void }) {
  const [latest, setLatest] = useState<IdeaGraph | null | undefined>(undefined);
  const [job, setJob] = useState<IdeaGraph | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [view, setView] = useState<'list' | 'map'>('list');
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getLatestIdeaGraph(itemId, controller.signal).then(setLatest).catch(() => { if (!controller.signal.aborted) setLatest(null); });
    return () => controller.abort();
  }, [itemId]);

  const jobId = job && !DONE.has(job.state) ? job.id : null;
  usePollJob(jobId, getIdeaGraph, DONE, (next) => { if (next.state === 'succeeded') setLatest(next); setJob(next); });

  async function generate() {
    setProblem(null);
    try {
      const graph = await requestIdeaGraph(itemId);
      setJob(graph);
      if (graph.state === 'succeeded') setLatest(graph);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not start the idea graph.';
      setProblem(error instanceof ApiRequestError && error.status === 409 && message.includes('AI') ? 'Local AI isn’t set up, so the idea graph is unavailable.' : message);
    }
  }

  if (latest === undefined) return <p className="summary-state">Loading idea graph…</p>;
  const running = Boolean(jobId);
  const failed = job && (job.state === 'failed' || job.state === 'interrupted') ? job : null;
  const stale = Boolean(latest && latest.summary_id !== summary.id);
  const canGenerate = !running && (!latest || stale || failed);
  const label = new Map(latest?.nodes.map((node) => [node.id, node.label]));
  const evidence = (startMs: number, what: string) => (
    <button aria-label={`Play evidence for ${what} at ${cueTime(startMs)}`} className="g-chip" onClick={() => onSeek(startMs / 1000)} type="button">{cueTime(startMs)}</button>
  );

  return (
    <div className="summary-panel idea-graph">
      {latest ? (
        <article className="summary-card">
          <div className="idea-graph__head">
            <p className="summary-provenance">
              Generated locally · {latest.model_id} · {latest.nodes.length} ideas from the summary’s cited transcript lines
              {stale ? <strong className="summary-stale"> · From an older summary</strong> : null}
            </p>
            <div aria-label="Idea graph view" className="idea-graph__views" role="group">
              <button aria-pressed={view === 'list'} className="g-text-button" onClick={() => setView('list')} type="button">List</button>
              <button aria-pressed={view === 'map'} className="g-text-button" onClick={() => setView('map')} type="button">Map</button>
            </div>
          </div>
          {view === 'map' ? (
            <IdeaMap graph={latest} onSelect={setSelected} selected={selected} />
          ) : null}
          <h3>Ideas</h3>
          <ul className="summary-points">
            {latest.nodes.map((node, index) => view === 'list' || !selected || node.id === selected ? (
              <li key={node.id}><span>{view === 'map' ? `${index + 1}. ` : null}{node.label}</span>{evidence(node.start_ms, node.label)}</li>
            ) : null)}
          </ul>
          {latest.edges.length ? (
            <>
              <h3>Connections</h3>
              <ul className="summary-points">
                {latest.edges.filter((edge) => view === 'list' || !selected || edge.source === selected || edge.target === selected).map((edge) => {
                  const text = `${label.get(edge.source)} — ${edge.label} → ${label.get(edge.target)}`;
                  return <li key={`${edge.source}-${edge.target}`}><span>{text}</span>{evidence(edge.start_ms, text)}</li>;
                })}
              </ul>
            </>
          ) : null}
          {latest.dropped ? <p className="summary-note">{latest.dropped} idea{latest.dropped === 1 ? '' : 's'} or connection{latest.dropped === 1 ? '' : 's'} omitted: no supporting transcript evidence.</p> : null}
        </article>
      ) : !running ? <p className="summary-state">No idea graph yet. Map the ideas in this video from its summary on your local model.</p> : null}
      {problem ? <p className="summary-state" role="alert">{problem}</p> : null}
      {running ? <p className="summary-state" role="status"><LoaderCircle className="spin" /> Mapping ideas on your local model…</p> : null}
      {failed ? <p className="summary-state" role="alert">{failed.state === 'interrupted' ? 'The idea graph was interrupted by a restart.' : `The idea graph failed${failed.error ? `: ${failed.error}` : '.'}`}</p> : null}
      {canGenerate ? (
        <button className="g-button" onClick={() => void generate()} type="button">
          {failed ? <RotateCcw /> : <Sparkles />}{failed ? 'Retry idea graph' : stale ? 'Map the latest summary' : 'Map ideas'}
        </button>
      ) : null}
    </div>
  );
}

function IdeaMap({ graph, selected, onSelect }: { graph: IdeaGraph; selected: string | null; onSelect: (id: string | null) => void }) {
  const layout = radialLayout(graph.nodes.length);
  const at = new Map(graph.nodes.map((node, index) => [node.id, layout[index]]));
  const touches = (id: string) => !selected || id === selected || graph.edges.some((edge) => (edge.source === selected && edge.target === id) || (edge.target === selected && edge.source === id));
  return (
    <div className="idea-map">
      <svg aria-hidden="true" preserveAspectRatio="none" viewBox="0 0 100 100">
        {graph.edges.map((edge) => {
          const from = at.get(edge.source)!;
          const to = at.get(edge.target)!;
          const lit = selected && (edge.source === selected || edge.target === selected);
          return <line className={lit ? 'lit' : undefined} key={`${edge.source}-${edge.target}`} x1={from.x} x2={to.x} y1={from.y} y2={to.y} />;
        })}
      </svg>
      {graph.nodes.map((node, index) => (
        <button
          aria-pressed={node.id === selected}
          className={touches(node.id) ? undefined : 'dim'}
          key={node.id}
          onClick={() => onSelect(node.id === selected ? null : node.id)}
          style={{ left: `${layout[index].x}%`, top: `${layout[index].y}%` }}
          type="button"
        ><span className="idea-map__index">{index + 1}</span> <span className="idea-map__label">{node.label}</span></button>
      ))}
    </div>
  );
}
