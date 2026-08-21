import { describe, expect, it } from 'vitest';
import { basename, formatBytes, formatDuration, shortenPath, titleCase } from './format';

describe('format helpers', () => {
  it('formats bytes at sensible precision', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(15 * 1024)).toBe('15 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('formats durations', () => {
    expect(formatDuration(120)).toBe('120 ms');
    expect(formatDuration(1500)).toBe('1.5 s');
    expect(formatDuration(65_000)).toBe('1m 5s');
  });

  it('shortens long paths while keeping both ends readable', () => {
    const long = '/Users/me/Documents/projects/analytics/reports/2026/q3/final_report.md';
    const short = shortenPath(long, 40);
    expect(short).toContain('final_report.md');
    expect(short).toContain('…');
    expect(shortenPath('/short/path.md')).toBe('/short/path.md');
  });

  it('extracts a basename and title-cases identifiers', () => {
    expect(basename('/a/b/c.txt')).toBe('c.txt');
    expect(titleCase('files.search')).toBe('Files Search');
  });
});
