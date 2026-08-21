import { Component, type ErrorInfo, type ReactNode, useMemo, useState } from 'react';
import { appStore, navigate } from '@/lib/store';
import { useHotkeys } from '@/hooks/useHotkeys';
import { Sidebar } from '@/components/Sidebar';
import { StatusBar } from '@/components/StatusBar';
import { CommandPalette } from '@/components/CommandPalette';
import { Toasts } from '@/components/Toasts';
import { ErrorState } from '@/components/primitives';
import { Home } from '@/screens/Home';
import { Conversation } from '@/screens/Conversation';
import { Activity } from '@/screens/Activity';
import { Files } from '@/screens/Files';
import { Notes } from '@/screens/Notes';
import { Privacy } from '@/screens/Privacy';
import { Settings } from '@/screens/Settings';
import { Browser, Calendar, Email, Memory, Terminal } from '@/screens/ToolScreens';

/** A render error in one screen must not take down the whole app. */
class ScreenBoundary extends Component<
  { children: ReactNode; screen: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('Screen crashed', this.props.screen, error, info.componentStack);
  }

  componentDidUpdate(previous: { screen: string }) {
    if (previous.screen !== this.props.screen && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-6">
          <ErrorState
            title="This screen ran into a problem"
            detail={this.state.error.message}
            onRetry={() => this.setState({ error: null })}
          />
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const screen = appStore.useStore((state) => state.screen);
  const [micActive] = useState(false);

  useHotkeys(
    useMemo(
      () => ({
        'mod+k': () => appStore.setState((state) => ({ paletteOpen: !state.paletteOpen })),
        'mod+/': () => appStore.setState({ paletteOpen: true }),
        'mod+n': () => appStore.setState({ sessionId: null, screen: 'conversation' }),
        'mod+1': () => navigate('home'),
        'mod+2': () => navigate('conversation'),
        'mod+3': () => navigate('activity'),
        'mod+4': () => navigate('files'),
        'mod+5': () => navigate('notes'),
        'mod+,': () => navigate('settings'),
        'mod+shift+p': () => navigate('privacy'),
      }),
      [],
    ),
  );

  const Screen = SCREENS[screen] ?? Home;

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-graphite-950">
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-hidden">
          <ScreenBoundary screen={screen}>
            <Screen />
          </ScreenBoundary>
        </main>
      </div>
      <StatusBar micActive={micActive} />
      <CommandPalette />
      <Toasts />
    </div>
  );
}

const SCREENS: Record<string, () => JSX.Element> = {
  home: Home,
  conversation: Conversation,
  activity: Activity,
  files: Files,
  notes: Notes,
  calendar: Calendar,
  email: Email,
  browser: Browser,
  terminal: Terminal,
  memory: Memory,
  privacy: Privacy,
  settings: Settings,
};
