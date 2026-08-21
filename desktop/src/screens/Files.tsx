import { useState } from 'react';
import { useAsync } from '@/hooks/useAsync';
import { ApiRequestError, api } from '@/lib/api';
import type { FileEntry } from '@/lib/types';
import { formatBytes, formatRelative } from '@/lib/format';
import { EmptyState, ErrorState, Icon, SkeletonList } from '@/components/primitives';
import { navigate } from '@/lib/store';

export function Files() {
  const roots = useAsync(() => api.fileRoots(), []);
  const [path, setPath] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [searchContents, setSearchContents] = useState(false);
  const [preview, setPreview] = useState<{ path: string; text: string; truncated: boolean } | null>(
    null,
  );
  const [error, setError] = useState<ApiRequestError | null>(null);

  const listing = useAsync(async () => (path ? api.listDirectory(path) : null), [path]);
  const results = useAsync(
    async () => (query.trim() ? api.searchFiles(query.trim(), searchContents) : null),
    [query, searchContents],
  );

  const open = async (entry: FileEntry) => {
    setError(null);
    if (entry.is_dir) {
      setPath(entry.path);
      setPreview(null);
      return;
    }
    try {
      const content = await api.readFile(entry.path);
      setPreview(content);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? caught : null);
    }
  };

  const entries = query.trim() ? (results.data?.files ?? []) : (listing.data?.entries ?? []);
  const loading = query.trim() ? results.loading : listing.loading;

  return (
    <div className="flex h-full">
      <div className="flex w-1/2 flex-col border-r border-graphite-850">
        <header className="space-y-3 border-b border-graphite-850 px-5 py-4">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-graphite-100">Files</h1>
            <p className="mt-0.5 text-sm text-graphite-500">
              Only the folders you have allowed. Everything else is invisible to PRIVIA.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Icon
                name="search"
                className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-graphite-600"
              />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search file names…"
                aria-label="Search files"
                className="field pl-8"
              />
            </div>
            <label className="flex items-center gap-1.5 text-2xs text-graphite-500">
              <input
                type="checkbox"
                checked={searchContents}
                onChange={(event) => setSearchContents(event.target.checked)}
                className="h-3 w-3"
              />
              contents
            </label>
          </div>
        </header>

        <div className="border-b border-graphite-850 px-5 py-2">
          {roots.loading ? (
            <div className="h-5 w-40 skeleton" />
          ) : roots.data && roots.data.roots.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5">
              {roots.data.roots.map((root) => (
                <button
                  key={root.path}
                  type="button"
                  onClick={() => {
                    setQuery('');
                    setPath(root.path);
                  }}
                  className={`chip ${
                    path === root.path
                      ? 'border-accent/40 bg-accent-soft text-accent'
                      : 'border-graphite-700 text-graphite-400 hover:text-graphite-200'
                  }`}
                >
                  {root.name}
                </button>
              ))}
              {path ? (
                <span className="ml-1 truncate font-mono text-2xs text-graphite-600" title={path}>
                  {path}
                </span>
              ) : null}
            </div>
          ) : (
            <p className="text-2xs text-graphite-600">
              No folders allowed yet.{' '}
              <button
                type="button"
                onClick={() => navigate('privacy')}
                className="text-accent underline"
              >
                Allow one in the Privacy Center
              </button>
            </p>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-2">
          {loading ? (
            <SkeletonList rows={6} />
          ) : entries.length === 0 ? (
            <EmptyState
              title={query ? 'Nothing matched' : 'Pick a folder above'}
              detail={
                query
                  ? 'Try fewer words, or switch on content search.'
                  : 'PRIVIA can only see folders you have explicitly allowed.'
              }
            />
          ) : (
            <ul className="space-y-0.5">
              {entries.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    onClick={() => void open(entry)}
                    className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-graphite-850 ${
                      preview?.path === entry.path ? 'bg-graphite-850' : ''
                    }`}
                  >
                    <Icon
                      name={entry.is_dir ? 'files' : 'note'}
                      className="h-3.5 w-3.5 shrink-0 text-graphite-600"
                    />
                    <span className="min-w-0 flex-1 truncate text-sm text-graphite-200">
                      {entry.name}
                    </span>
                    {!entry.is_dir ? (
                      <span className="shrink-0 text-2xs text-graphite-600">
                        {formatBytes(entry.size_bytes)}
                      </span>
                    ) : null}
                    <span className="w-14 shrink-0 text-right text-2xs text-graphite-600">
                      {formatRelative(entry.modified_at)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="flex w-1/2 flex-col">
        {error ? (
          <div className="p-5">
            <ErrorState title="That file cannot be read" detail={error.message} code={error.code} />
          </div>
        ) : preview ? (
          <>
            <header className="flex items-center gap-2 border-b border-graphite-850 px-5 py-3">
              <Icon name="eye" className="h-3.5 w-3.5 text-graphite-600" />
              <p className="min-w-0 flex-1 truncate font-mono text-2xs text-graphite-400">
                {preview.path}
              </p>
              {preview.truncated ? (
                <span className="chip border-caution/40 text-caution">truncated</span>
              ) : null}
              <button type="button" className="btn-quiet" onClick={() => setPreview(null)}>
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </header>
            <pre className="flex-1 overflow-auto whitespace-pre-wrap break-words px-5 py-4 font-mono text-2xs leading-relaxed text-graphite-300">
              {preview.text}
            </pre>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              title="Select a file"
              detail="Reading a file is recorded in the activity log, so you can always see what was opened."
              icon={<Icon name="eye" className="h-6 w-6" />}
            />
          </div>
        )}
      </div>
    </div>
  );
}
