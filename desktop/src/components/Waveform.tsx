/** A live level meter shown only while the microphone is actually open. */

import { useEffect, useRef } from 'react';

export function Waveform({ level, active }: { level: number; active: boolean }) {
  const history = useRef<number[]>(Array.from({ length: 40 }, () => 0));
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!active) {
      history.current = history.current.map(() => 0);
    }
  }, [active]);

  useEffect(() => {
    history.current = [...history.current.slice(1), Math.min(1, level)];
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;

    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const bars = history.current.length;
    const barWidth = width / bars;
    context.fillStyle = active ? '#5eb3a1' : '#3a424a';
    history.current.forEach((value, index) => {
      const barHeight = Math.max(2, value * height * 0.9);
      context.globalAlpha = 0.35 + (index / bars) * 0.65;
      context.fillRect(
        index * barWidth + barWidth * 0.2,
        (height - barHeight) / 2,
        barWidth * 0.6,
        barHeight,
      );
    });
    context.globalAlpha = 1;
  }, [level, active]);

  return (
    <canvas
      ref={canvasRef}
      className="h-6 w-32"
      role="img"
      aria-label={active ? 'Microphone level' : 'Microphone idle'}
    />
  );
}
