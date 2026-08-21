import { useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { api } from '@/lib/api';
import type { Note } from '@/lib/types';
import { formatRelative } from '@/lib/format';
import { pushToast } from '@/lib/store';
import { Chip, EmptyState, Icon, SkeletonList } from '@/components/primitives';

export function Notes() {
  const [query, setQuery] = useState('');
  const { data, loading, reload } = useAsync(() => api.notes(query), [query]);
  const [selected, setSelected] = useState<Note | null>(null);
  const [draft, setDraft] = useState({ title: '', body: '' });
  const [saving, setSaving] = useState(false);

  const notes = data?.notes ?? [];

  const save = async () => {
    setSaving(true);
    try {
      if (selected) {
        const updated = await api.updateNote(selected.id, {
          title: draft.title,
          body: draft.body,
        });
        setSelected(updated);
      } else {
        const created = await api.createNote(draft.title || 'Untitled', draft.body);
        setSelected(created);
      }
      pushToast({ kind: 'success', title: 'Note saved' });
      reload();
    } catch (caught) {
      pushToast({
        kind: 'error',
        title: 'Could not save',
        detail: caught instanceof Error ? caught.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex w-72 shrink-0 flex-col border-r border-graphite-850">
        <header className="space-y-2.5 border-b border-graphite-850 px-4 py-4">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold tracking-tight text-graphite-100">Notes</h1>
            <button
              type="button"
              className="btn-quiet"
              aria-label="New note"
              onClick={() => {
                setSelected(null);
                setDraft({ title: '', body: '' });
              }}
            >
              <Icon name="plus" />
            </button>
          </div>
          <div className="relative">
            <Icon
              name="search"
              className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-graphite-600"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search notes…"
              aria-label="Search notes"
              className="field pl-8"
            />
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-2">
          {loading ? (
            <SkeletonList rows={5} />
          ) : notes.length === 0 ? (
            <EmptyState
              title="No notes yet"
              detail="Notes are stored locally and full-text searchable."
            />
          ) : (
            <ul className="space-y-0.5">
              {notes.map((note) => (
                <li key={note.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelected(note);
                      setDraft({ title: note.title, body: note.body });
                    }}
                    className={`w-full rounded-lg px-2.5 py-2 text-left transition-colors ${
                      selected?.id === note.id ? 'bg-graphite-800' : 'hover:bg-graphite-850'
                    }`}
                  >
                    <p className="truncate text-sm text-graphite-200">{note.title}</p>
                    <p className="mt-0.5 truncate text-2xs text-graphite-600">
                      {formatRelative(note.updated_at)}
                      {note.tags.length ? ` · ${note.tags.join(', ')}` : ''}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="flex flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-graphite-850 px-5 py-3">
          <input
            value={draft.title}
            onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
            placeholder="Title"
            aria-label="Note title"
            className="flex-1 bg-transparent text-sm font-medium text-graphite-100 placeholder:text-graphite-600 focus:outline-none"
          />
          {selected ? <Chip>{formatRelative(selected.updated_at)}</Chip> : null}
          <button
            type="button"
            className="btn-primary"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
          {selected ? (
            <button
              type="button"
              className="btn-danger"
              onClick={async () => {
                await api.deleteNote(selected.id);
                setSelected(null);
                setDraft({ title: '', body: '' });
                pushToast({ kind: 'success', title: 'Note deleted' });
                reload();
              }}
            >
              <Icon name="trash" className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </header>
        <textarea
          value={draft.body}
          onChange={(event) => setDraft((current) => ({ ...current, body: event.target.value }))}
          placeholder="Write here. Markdown is fine — it is stored as plain text on this machine."
          aria-label="Note body"
          className="flex-1 resize-none bg-transparent px-5 py-4 text-sm leading-relaxed text-graphite-200 placeholder:text-graphite-600 focus:outline-none"
        />
      </div>
    </div>
  );
}
