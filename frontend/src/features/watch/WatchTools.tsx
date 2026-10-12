import { type KeyboardEvent, type ReactNode, useId, useState } from 'react';
import './tools.css';

export type WatchTool = { id: string; label: string; panel: ReactNode };

/**
 * The tool region under the player: one principal panel open at a time.
 * Callers list only tools that work for the current media (null = unavailable),
 * so no empty placeholder tab ever renders; new tools (transcript, summary…)
 * join by adding an entry. Inactive panels stay mounted but hidden, so drafts
 * and scroll state survive tab switches and the player is never touched.
 */
export function WatchTools({ tools: candidates }: { tools: Array<WatchTool | null> }) {
  const tools = candidates.filter((tool): tool is WatchTool => tool !== null);
  const [selected, setSelected] = useState(tools[0]?.id);
  const baseId = useId().replace(/:/g, '');
  if (!tools.length) return null;
  const active = tools.some((tool) => tool.id === selected) ? selected : tools[0].id;
  const tabbed = tools.length > 1;
  const tabId = (id: string) => `${baseId}-${id}-tab`;

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = tools.length - 1;
    const next = event.key === 'ArrowRight' ? (index === last ? 0 : index + 1)
      : event.key === 'ArrowLeft' ? (index === 0 ? last : index - 1)
        : event.key === 'Home' ? 0 : event.key === 'End' ? last : -1;
    if (next < 0) return;
    event.preventDefault();
    setSelected(tools[next].id);
    document.getElementById(tabId(tools[next].id))?.focus();
  }

  return (
    <section aria-label="Video tools" className="watch-tools">
      {tabbed ? (
        <div aria-label="Video tools" className="g-tabs watch-tools-tabs" role="tablist">
          {tools.map((tool, index) => (
            <button
              aria-controls={`${baseId}-${tool.id}-panel`}
              aria-selected={tool.id === active}
              id={tabId(tool.id)}
              key={tool.id}
              onClick={() => setSelected(tool.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
              role="tab"
              tabIndex={tool.id === active ? 0 : -1}
              type="button"
            >{tool.label}</button>
          ))}
        </div>
      ) : null}
      {tools.map((tool) => (
        <div aria-labelledby={tabbed ? tabId(tool.id) : undefined} className="watch-tools-panel" hidden={tool.id !== active} id={`${baseId}-${tool.id}-panel`} key={tool.id} role={tabbed ? 'tabpanel' : undefined}>
          {tool.panel}
        </div>
      ))}
    </section>
  );
}
