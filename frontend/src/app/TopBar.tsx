import { Menu as MenuIcon, Plus, Search } from 'lucide-react';
import { type CSSProperties, type KeyboardEvent, type RefObject } from 'react';
import { providerBlocked, useAccess } from '../features/access/access';
import { openMemberPicker } from './commands';
import { useDeviceRingSize } from '../features/auth/memberPicker';
import type { ThemePreference } from '../theme';
import type { UserProfile } from '../types';
import { Avatar, BrandMark, Button, IconButton, Kbd, Menu, modKeyLabel, Popover, StatusText } from '../ui';
import type { CollectionLoadState } from '../workspace';

export interface TopBarProps {
  user: UserProfile;
  mobile: boolean;
  drawer: boolean;
  compact: boolean;
  menuLabel: 'Open navigation' | 'Close navigation';
  menuExpanded: boolean;
  menuButtonRef: RefObject<HTMLButtonElement | null>;
  activity: { state: CollectionLoadState; retrying: boolean; active: number };
  theme: ThemePreference;
  onMenu: () => void;
  onMenuKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  onHome: () => void;
  onSearch: () => void;
  onAddLink: () => void;
  onActivity: () => void;
  onSettings: () => void;
  onTheme: (theme: ThemePreference) => void;
  onSignOut: () => void;
}

/** The existing activity wording. */
export function activityLabel({ state, retrying, active }: TopBarProps['activity']): string {
  if (retrying) return 'Refreshing download activity';
  if (state === 'loading') return 'Checking download activity';
  if (state === 'stale') return 'Download activity may be out of date';
  if (state === 'offline' || state === 'failed') return 'Download activity unavailable';
  return active ? `${active} active downloads` : 'No active downloads';
}

export function TopBar(props: TopBarProps) {
  const { user, mobile, compact, activity, theme } = props;
  const ringSize = useDeviceRingSize();
  const canAddLink = !providerBlocked(useAccess().access, 'open_search');
  const name = user.display_name || user.username;
  const current = (activity.state === 'ready' || activity.state === 'empty') && !activity.retrying;
  const active = current ? activity.active : 0;
  const label = activityLabel(activity);
  const shortcut = modKeyLabel();
  const activityButton = (triggerProps: Record<string, unknown> = {}) => (
    <button onClick={props.onActivity} {...triggerProps} aria-label={label} className="g-activity" type="button">
      <span aria-hidden="true" className="g-activity-ring" style={{ '--progress': active ? '288deg' : '0deg' } as CSSProperties} />
      {active ? <span aria-hidden="true" className="g-activity-count">{active}</span> : null}
    </button>
  );
  return (
    <header className="g-topbar">
      <div className="g-topbar-start">
        <IconButton aria-controls="primary-navigation" aria-expanded={props.menuExpanded} aria-haspopup={props.drawer ? 'dialog' : undefined} icon={<MenuIcon />} label={props.menuLabel} onClick={props.onMenu} onKeyDown={props.onMenuKeyDown} ref={props.menuButtonRef} />
        <a className="g-topbar-brand" href="/" onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); props.onHome(); }}>
          <BrandMark compact={compact} size="bar" />
        </a>
      </div>
      <button aria-label={`Search Lumina, ${shortcut}`} className="g-search-trigger" onClick={props.onSearch} type="button">
        <Search aria-hidden="true" />
        <span className="g-search-trigger-text">Search Lumina</span>
        <Kbd>{shortcut}</Kbd>
      </button>
      <div className="g-topbar-end">
        {!canAddLink ? null : mobile ? <IconButton icon={<Plus />} label="Add a link" onClick={props.onAddLink} /> : <Button aria-label="Add a link" icon={<Plus />} onClick={props.onAddLink} variant="quiet">Add link</Button>}
        {current ? activityButton() : (
          <Popover label="Download activity" openOnHover trigger={(triggerProps) => activityButton(triggerProps)}>
            <StatusText tone="attention">{label}</StatusText>
            <Button onClick={props.onActivity} variant="quiet">Open Downloads</Button>
          </Popover>
        )}
        <Menu
          align="end"
          items={[
            { kind: 'group', label: `${name} · ${user.role === 'admin' ? 'Vault owner' : 'Household member'}` },
            { kind: 'item', label: ringSize === 0 ? 'Sign in as someone else…' : 'Switch member…', onSelect: openMemberPicker },
            { kind: 'item', label: 'Settings', onSelect: props.onSettings },
            { kind: 'group', label: 'Theme' },
            { kind: 'radio', label: 'System', checked: theme === 'system', hint: theme === 'system' ? 'Current' : undefined, onSelect: () => props.onTheme('system') },
            { kind: 'radio', label: 'Light', checked: theme === 'light', hint: theme === 'light' ? 'Current' : undefined, onSelect: () => props.onTheme('light') },
            { kind: 'radio', label: 'Dark', checked: theme === 'dark', hint: theme === 'dark' ? 'Current' : undefined, onSelect: () => props.onTheme('dark') },
            { kind: 'separator' },
            { kind: 'item', label: 'Sign out', onSelect: props.onSignOut },
          ]}
          trigger={(triggerProps) => (
            <button {...triggerProps} aria-label={`${name}, account menu`} className="g-profile-trigger" type="button"><Avatar decorative name={name} size={34} /></button>
          )}
        />
      </div>
    </header>
  );
}
