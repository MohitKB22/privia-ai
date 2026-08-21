import { appStore, navigate, type Screen } from '@/lib/store';
import { Icon, type IconName } from './primitives';
import { MOD_LABEL } from '@/hooks/useHotkeys';

const GROUPS: { title: string; items: { screen: Screen; label: string; icon: IconName }[] }[] = [
  {
    title: 'Assistant',
    items: [
      { screen: 'home', label: 'Home', icon: 'home' },
      { screen: 'conversation', label: 'Conversation', icon: 'chat' },
      { screen: 'activity', label: 'Activity', icon: 'activity' },
    ],
  },
  {
    title: 'Your things',
    items: [
      { screen: 'files', label: 'Files', icon: 'files' },
      { screen: 'notes', label: 'Notes', icon: 'note' },
      { screen: 'calendar', label: 'Calendar', icon: 'calendar' },
      { screen: 'email', label: 'Email', icon: 'mail' },
    ],
  },
  {
    title: 'Tools',
    items: [
      { screen: 'browser', label: 'Browser', icon: 'globe' },
      { screen: 'terminal', label: 'Terminal', icon: 'terminal' },
      { screen: 'memory', label: 'Memory', icon: 'brain' },
    ],
  },
  {
    title: 'Control',
    items: [
      { screen: 'privacy', label: 'Privacy Center', icon: 'shield' },
      { screen: 'settings', label: 'Settings', icon: 'settings' },
    ],
  },
];

export function Sidebar() {
  const current = appStore.useStore((state) => state.screen);

  return (
    <nav
      aria-label="Main"
      className="flex w-56 shrink-0 flex-col border-r border-graphite-850 bg-graphite-900/50"
    >
      <div className="flex items-center gap-2.5 px-4 py-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-soft">
          <Icon name="shield" className="h-4 w-4 text-accent" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-tight text-graphite-100">PRIVIA</p>
          <p className="text-2xs text-graphite-600">Private Personal AI</p>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {GROUPS.map((group) => (
          <div key={group.title} className="mb-4">
            <p className="px-2 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-graphite-600">
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = current === item.screen;
                return (
                  <li key={item.screen}>
                    <button
                      type="button"
                      onClick={() => navigate(item.screen)}
                      aria-current={active ? 'page' : undefined}
                      className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm transition-colors ${
                        active
                          ? 'bg-graphite-800 text-graphite-100'
                          : 'text-graphite-400 hover:bg-graphite-850 hover:text-graphite-200'
                      }`}
                    >
                      <Icon
                        name={item.icon}
                        className={`h-4 w-4 ${active ? 'text-accent' : 'text-graphite-600'}`}
                      />
                      {item.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={() => appStore.setState({ paletteOpen: true })}
        className="mx-2 mb-3 flex items-center gap-2 rounded-lg border border-graphite-800 px-2 py-1.5 text-2xs text-graphite-500 transition-colors hover:border-graphite-700 hover:text-graphite-300"
      >
        <Icon name="search" className="h-3.5 w-3.5" />
        Commands
        <kbd className="ml-auto rounded border border-graphite-700 px-1 font-mono">
          {MOD_LABEL}K
        </kbd>
      </button>
    </nav>
  );
}
