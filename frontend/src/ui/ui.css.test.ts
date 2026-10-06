/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('src/ui/ui.css', 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ');
const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // every declaration block for the selector: the promoted base rules and later additions both count
  return [...css.matchAll(new RegExp(`(?:^|[}\\s])${escaped} \\{([^}]*)\\}`, 'g'))].map((m) => m[1]).join(' ');
};

describe('ui.css', () => {
  it.each(['.g-button', '.g-choice', '.g-switch', '.g-segment', '.g-text-button'])('%s is at least one control tall', (selector) => {
    expect(rule(selector)).toMatch(/min-height: var\(--g-control\)/);
  });
  it('.g-icon-button sizes from --g-control', () => {
    expect(rule('.g-icon-button')).toMatch(/(height|min-height): var\(--g-control\)/);
  });
  it('draws a text field as a value on one hairline rule, never a box, and keeps a 44px hit height', () => {
    const field = rule('.g-input, .g-select select');
    expect(field).toMatch(/min-height: max\(var\(--g-control\), 44px\)/);
    expect(field).toMatch(/--rule: var\(--g-field-rule\);.*border: 0; border-bottom: 1px solid var\(--rule\); border-radius: 0; background: transparent/);
    // focus: the rule turns gold and an outer shadow doubles it (no layout shift); invalid keeps the danger rule even while focused
    expect(rule('.g-input:focus, .g-select select:focus')).toMatch(/--rule: var\(--g-gold-ink\);.*box-shadow: 0 1px 0 var\(--rule\)/);
    expect(rule(".g-input[aria-invalid='true'], .g-select select[aria-invalid='true']")).toMatch(/--rule: var\(--g-danger\)/);
    expect(css.indexOf(".g-input[aria-invalid='true']")).toBeGreaterThan(css.indexOf('.g-input:focus'));
  });
  it('draws empty and error states open, never boxed or striped (craft floor)', () => {
    for (const selector of ['.g-empty', '.g-error-state']) expect(rule(selector)).not.toMatch(/border|background/);
  });
  it('starts a text button on the content edge and keeps its hit area in a pseudo-element', () => {
    expect(rule('.g-text-button')).toMatch(/padding: 0;/);
    expect(rule('.g-text-button::before')).toMatch(/position: absolute; inset: 0 -8px/);
  });
  it('marks a pressed chip with a gold underline only: no fill, no box', () => {
    expect(rule('.g-chip')).toMatch(/border: 0/);
    expect(rule(".g-chip[aria-pressed='true']")).toMatch(/text-decoration: underline 2px var\(--g-gold-ink\)/);
    expect(rule(".g-chip[aria-pressed='true']")).not.toMatch(/background/);
  });
  it('never lifts or scales on hover', () => {
    for (const match of css.matchAll(/[^{}]*:hover[^{}]*\{([^}]*)\}/g)) expect(match[1]).not.toMatch(/transform/);
  });
  it('keeps the switch rectangular and says On with more than colour', () => {
    expect(rule('.g-switch-track')).toMatch(/border-radius: var\(--g-radius\)/);
    expect(css).toContain('.g-switch input:checked + .g-switch-track::after');
  });
  it('draws the selected tab with weight and a gold rule', () => {
    expect(rule(".g-tabs [role='tab'][aria-selected='true']")).toMatch(/border-bottom-color: var\(--g-gold\).*font-weight: 700/);
  });
  it('keeps the primitive classes from restyling gallery and remote markup (class names are shared)', () => {
    expect(css).not.toMatch(/(?:^|[},])\s*\.g-avatar \{/); // ChannelAvatar owns the bare .g-avatar (channelAvatar.css)
    expect(css).not.toMatch(/(?:^|[},])\s*\.g-toast \{/); // the gallery's .gallery .g-toast (StillWall, LibraryBrowser) lives for now; ui scopes to .g-toast-region
    expect(css).not.toMatch(/\.gallery[^{}]*\.g-(?:menu|popover|toast|notice|dialog)/); // primitives mount wherever their trigger is: never scoped to .gallery
  });
  it('keeps a primary button readable in forced colours', () => {
    expect(css).toMatch(/@media \(forced-colors: active\) \{[^@]*\.g-button\.is-primary \{ border: 2px solid ButtonText; background: ButtonFace; color: ButtonText; \}/);
  });
  it('styles the menu and popover with global rules that need nothing from an ancestor', () => {
    expect(css).toMatch(/\.g-menu, \.g-popover \{[^}]*position: fixed[^}]*background: var\(--g-paper-3\)[^}]*box-shadow: var\(--g-elev-1\)/);
    expect(css).toMatch(/\.g-menu-item \{[^}]*min-height: var\(--g-control\)/);
    expect(css).not.toMatch(/\.gallery[^{}]*\.g-(?:menu|popover)/);
  });
});

describe('one button typography', () => {
  it('sits on .g-button itself, so a raw g-button and <Button> render the same type', () => {
    expect(rule('.g-button')).toMatch(/font: var\(--g-type-button\).*text-transform: uppercase/);
    expect(rule('.g-button-text')).not.toMatch(/font:|text-transform|letter-spacing/);
    expect(css).not.toContain('until T5');
  });
});
