/**
 * The main workspace: message, tool timeline, permission prompts, confirmation.
 *
 * The whole permission and confirmation dance lives here because it is a
 * property of the conversation, not of a settings page: the assistant asks in
 * context, the user answers in context, and the action resumes in context.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiRequestError, api } from '@/lib/api';
import { appStore, pushToast } from '@/lib/store';
import type { ChatMessage, ConfirmationRequest, PermissionPrompt as Prompt } from '@/lib/types';
import { formatDuration } from '@/lib/format';
import { useVoice } from '@/hooks/useVoice';
import { ConfirmDialog } from '@/components/ConfirmDialog';
import { PermissionPrompt } from '@/components/PermissionPrompt';
import { ToolTimeline } from '@/components/ToolTimeline';
import { Waveform } from '@/components/Waveform';
import { Chip, Icon } from '@/components/primitives';

const SUGGESTIONS = [
  'Find the project report and summarise it',
  'What is on my calendar tomorrow?',
  'Create a note called interview preparation',
  'What files did you access in this session?',
];

export function Conversation() {
  const sessionId = appStore.useStore((state) => state.sessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const [pendingText, setPendingText] = useState('');
  const [permission, setPermission] = useState<Prompt | null>(null);
  const [speak, setSpeak] = useState(false);

  const voice = useVoice();
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, confirmation, permission]);

  useEffect(() => {
    if (!sessionId) setMessages([]);
  }, [sessionId]);

  const append = useCallback((message: ChatMessage) => {
    setMessages((current) => [...current, message]);
  }, []);

  const send = useCallback(
    async (text: string, options: { confirmationId?: string; confirm?: boolean } = {}) => {
      const trimmed = text.trim();
      if (!trimmed || busy) return;

      if (!options.confirmationId) {
        append({
          id: `u-${Date.now()}`,
          role: 'user',
          content: trimmed,
          createdAt: Date.now(),
        });
        setInput('');
      }
      setBusy(true);
      setPermission(null);

      try {
        const response = await api.chat({
          message: trimmed,
          session_id: sessionId,
          confirmation_id: options.confirmationId ?? null,
          confirm: options.confirm ?? null,
          speak,
        });

        if (!sessionId) appStore.setState({ sessionId: response.session_id });

        append({
          id: response.run_id || `a-${Date.now()}`,
          role: 'assistant',
          content: response.response,
          createdAt: Date.now(),
          runId: response.run_id,
          toolCalls: response.tool_calls,
          toolResults: response.tool_results,
          accessed: response.accessed_resources,
          location: response.processing_location,
          model: response.model_used,
          durationMs: response.duration_ms,
          pending: response.status === 'awaiting_confirmation',
          failed: response.status === 'failed',
        });

        if (response.pending_confirmation) {
          setConfirmation(response.pending_confirmation);
          setPendingText(trimmed);
        } else {
          setConfirmation(null);
          setPendingText('');
        }
        if (response.permission_prompt) setPermission(response.permission_prompt);

        if (response.audio_base64) {
          const audio = new Audio(`data:audio/wav;base64,${response.audio_base64}`);
          audioRef.current?.pause();
          audioRef.current = audio;
          void audio.play().catch(() => undefined);
        }
      } catch (caught) {
        const error =
          caught instanceof ApiRequestError
            ? caught
            : new ApiRequestError('INTERNAL_ERROR', String(caught), 0);
        append({
          id: `e-${Date.now()}`,
          role: 'assistant',
          content: error.message,
          createdAt: Date.now(),
          failed: true,
        });
        if (error.isOffline) {
          pushToast({ kind: 'error', title: 'Backend unreachable', detail: error.message });
        }
      } finally {
        setBusy(false);
        inputRef.current?.focus();
      }
    },
    [append, busy, sessionId, speak],
  );

  const resolveConfirmation = useCallback(
    async (approved: boolean) => {
      if (!confirmation) return;
      const id = confirmation.id;
      const text = pendingText;
      setConfirmation(null);
      await send(text, { confirmationId: id, confirm: approved });
    },
    [confirmation, pendingText, send],
  );

  const grantPermission = useCallback(
    async (scopes: string[], resources: string[]) => {
      setBusy(true);
      try {
        for (const scope of scopes) await api.setPermission(scope, true, resources);
        pushToast({ kind: 'success', title: 'Permission granted', detail: scopes.join(', ') });
        setPermission(null);
        const last = [...messages].reverse().find((m) => m.role === 'user');
        if (last) await send(last.content);
      } catch (caught) {
        pushToast({
          kind: 'error',
          title: 'Could not grant that',
          detail: caught instanceof Error ? caught.message : undefined,
        });
      } finally {
        setBusy(false);
      }
    },
    [messages, send],
  );

  const handleMicUp = useCallback(async () => {
    const text = await voice.stop();
    if (text) void send(text);
  }, [send, voice]);

  return (
    <div className="flex h-full flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-3xl space-y-4">
          {messages.length === 0 ? (
            <div className="pt-10 text-center">
              <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-xl bg-accent-soft">
                <Icon name="shield" className="h-5 w-5 text-accent" />
              </div>
              <h1 className="text-lg font-semibold text-graphite-100">What can I do for you?</h1>
              <p className="mt-1.5 text-sm text-graphite-500">
                Everything runs on this machine. I ask before anything with consequences.
              </p>
              <div className="mx-auto mt-6 grid max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => void send(suggestion)}
                    className="rounded-lg border border-graphite-800 px-3 py-2.5 text-left text-sm text-graphite-400 transition-colors hover:border-graphite-700 hover:text-graphite-200"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}

          {busy ? (
            <div className="flex items-center gap-2 text-sm text-graphite-500">
              <span className="flex gap-1">
                {[0, 1, 2].map((index) => (
                  <span
                    key={index}
                    className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-graphite-500"
                    style={{ animationDelay: `${index * 160}ms` }}
                  />
                ))}
              </span>
              Working locally…
            </div>
          ) : null}

          {permission ? (
            <PermissionPrompt
              prompt={permission}
              busy={busy}
              onAllow={(scopes, resources) => void grantPermission(scopes, resources)}
              onDeny={() => setPermission(null)}
            />
          ) : null}
        </div>
      </div>

      <div className="border-t border-graphite-850 bg-graphite-900/40 px-6 py-3">
        <div className="mx-auto max-w-3xl">
          {voice.error ? (
            <div className="mb-2 flex items-center gap-2 text-2xs text-caution">
              <Icon name="alert" className="h-3.5 w-3.5" />
              {voice.error}
              <button
                type="button"
                onClick={voice.clearError}
                className="ml-auto text-graphite-500 hover:text-graphite-300"
              >
                dismiss
              </button>
            </div>
          ) : null}

          <div className="flex items-end gap-2 rounded-xl border border-graphite-700 bg-graphite-850 px-3 py-2 focus-within:border-accent/40">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void send(input);
                }
              }}
              rows={1}
              placeholder="Ask, or hold the microphone to speak…"
              aria-label="Message"
              disabled={busy}
              className="max-h-40 flex-1 resize-none bg-transparent py-1 text-sm text-graphite-100 placeholder:text-graphite-600 focus:outline-none"
              style={{ height: 'auto' }}
              onInput={(event) => {
                const element = event.currentTarget;
                element.style.height = 'auto';
                element.style.height = `${Math.min(160, element.scrollHeight)}px`;
              }}
            />

            {voice.state === 'recording' ? <Waveform level={voice.level} active /> : null}

            <button
              type="button"
              aria-label="Hold to talk"
              aria-pressed={voice.state === 'recording'}
              disabled={voice.state === 'unsupported' || voice.state === 'transcribing' || busy}
              onPointerDown={() => void voice.start()}
              onPointerUp={() => void handleMicUp()}
              onPointerLeave={() => {
                if (voice.state === 'recording') void handleMicUp();
              }}
              className={`btn h-8 w-8 rounded-lg p-0 ${
                voice.state === 'recording'
                  ? 'bg-danger text-graphite-950'
                  : 'text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100'
              }`}
              title={
                voice.state === 'unsupported'
                  ? 'This browser has no microphone API'
                  : 'Hold to talk'
              }
            >
              <Icon name="mic" />
            </button>

            <button
              type="button"
              onClick={() => void send(input)}
              disabled={!input.trim() || busy}
              aria-label="Send"
              className="btn-primary h-8 w-8 p-0"
            >
              <Icon name="send" />
            </button>
          </div>

          <div className="mt-1.5 flex items-center gap-3 text-2xs text-graphite-600">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                checked={speak}
                onChange={(event) => setSpeak(event.target.checked)}
                className="h-3 w-3"
              />
              Speak replies
            </label>
            <span className="ml-auto">Enter to send · Shift+Enter for a new line</span>
          </div>
        </div>
      </div>

      {confirmation ? (
        <ConfirmDialog
          request={confirmation}
          busy={busy}
          onApprove={() => void resolveConfirmation(true)}
          onReject={() => void resolveConfirmation(false)}
        />
      ) : null}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] animate-fade-in rounded-2xl rounded-br-md bg-graphite-800 px-3.5 py-2 text-sm text-graphite-100">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <div
        className={`rounded-2xl rounded-bl-md border px-3.5 py-2.5 ${
          message.failed
            ? 'border-danger/30 bg-danger-soft'
            : 'border-graphite-800 bg-graphite-900/60'
        }`}
      >
        <div className="prose-privia whitespace-pre-wrap break-words">{message.content}</div>

        {message.toolCalls?.length || message.accessed?.length ? (
          <ToolTimeline
            calls={message.toolCalls ?? []}
            results={message.toolResults ?? []}
            accessed={message.accessed ?? []}
          />
        ) : null}

        <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs text-graphite-600">
          {message.location ? (
            <Chip tone={message.location === 'cloud' ? 'caution' : 'accent'}>
              <Icon name={message.location === 'cloud' ? 'cloud' : 'lock'} className="h-3 w-3" />
              {message.location === 'cloud' ? 'sent to cloud' : 'processed locally'}
            </Chip>
          ) : null}
          {message.model ? <span className="font-mono">{message.model}</span> : null}
          {message.durationMs ? <span>{formatDuration(message.durationMs)}</span> : null}
          {message.pending ? <Chip tone="caution">waiting for you</Chip> : null}
        </div>
      </div>
    </div>
  );
}
