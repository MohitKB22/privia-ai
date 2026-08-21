import { useCallback, useRef, useState } from 'react';
import { api } from '@/lib/api';

export type MicState =
  'idle' | 'requesting' | 'recording' | 'transcribing' | 'denied' | 'unsupported';

/**
 * Push-to-talk recording.
 *
 * The microphone is opened when recording starts and every track is stopped the
 * moment it ends, so the OS indicator reflects reality: PRIVIA is not listening
 * unless the button is held. Audio is encoded to 16-bit PCM WAV in the browser
 * and posted once; nothing is streamed continuously.
 */
export function useVoice() {
  const [state, setState] = useState<MicState>(
    typeof navigator !== 'undefined' && navigator.mediaDevices ? 'idle' : 'unsupported',
  );
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const rafRef = useRef<number | null>(null);

  const cleanup = useCallback(() => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close().catch(() => undefined);
    contextRef.current = null;
    setLevel(0);
  }, []);

  const start = useCallback(async () => {
    if (state === 'unsupported') return;
    setError(null);
    setState('requesting');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
      const context = new AudioContext();
      contextRef.current = context;
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      const processor = context.createScriptProcessor(4096, 1, 1);
      chunksRef.current = [];

      processor.onaudioprocess = (event) => {
        chunksRef.current.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };
      source.connect(analyser);
      analyser.connect(processor);
      processor.connect(context.destination);

      const buffer = new Uint8Array(analyser.frequencyBinCount);
      const measure = () => {
        analyser.getByteTimeDomainData(buffer);
        let peak = 0;
        for (const sample of buffer) peak = Math.max(peak, Math.abs(sample - 128) / 128);
        setLevel(peak);
        rafRef.current = requestAnimationFrame(measure);
      };
      measure();
      setState('recording');
    } catch (caught) {
      cleanup();
      const name = (caught as DOMException)?.name;
      setState(name === 'NotAllowedError' ? 'denied' : 'idle');
      setError(
        name === 'NotAllowedError'
          ? 'Microphone access was denied. You can keep typing.'
          : 'No microphone is available. You can keep typing.',
      );
    }
  }, [cleanup, state]);

  const stop = useCallback(async (): Promise<string | null> => {
    if (state !== 'recording') {
      cleanup();
      return null;
    }
    const sampleRate = contextRef.current?.sampleRate ?? 44_100;
    const chunks = chunksRef.current;
    chunksRef.current = [];
    cleanup();
    setState('transcribing');
    try {
      const wav = encodeWav(chunks, sampleRate);
      if (wav.size < 2048) {
        setState('idle');
        setError('That was too short to hear. Hold the button while you speak.');
        return null;
      }
      const result = await api.transcribe(wav);
      setState('idle');
      if (!result.speech_detected) {
        setError(result.message ?? 'I did not hear anything.');
        return null;
      }
      return result.text;
    } catch (caught) {
      setState('idle');
      setError(caught instanceof Error ? caught.message : 'Transcription failed.');
      return null;
    }
  }, [cleanup, state]);

  return { state, level, error, start, stop, clearError: () => setError(null) };
}

/** Encode Float32 chunks to a 16-bit PCM WAV blob. */
function encodeWav(chunks: Float32Array[], sampleRate: number): Blob {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);

  const writeString = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeString(0, 'RIFF');
  view.setUint32(4, 36 + length * 2, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, length * 2, true);

  let offset = 44;
  for (const chunk of chunks) {
    for (const sample of chunk) {
      const clamped = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: 'audio/wav' });
}
