/**
 * The confirmation dialog is the last line of defence before a side effect, so
 * its behaviour is tested rather than assumed.
 */

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './ConfirmDialog';
import type { ConfirmationRequest } from '@/lib/types';

const destructive: ConfirmationRequest = {
  id: 'cfm_1',
  run_id: 'run_1',
  tool_name: 'files.delete',
  title: 'Permanently delete this file?',
  summary: 'Delete /home/me/notes.md. This cannot be undone.',
  risk_level: 'critical',
  details: { Path: '/home/me/notes.md', Recoverable: 'No.' },
  target: '/home/me/notes.md',
  destructive: true,
};

const benign: ConfirmationRequest = { ...destructive, destructive: false, risk_level: 'medium' };

describe('ConfirmDialog', () => {
  it('shows the exact target and every detail row', () => {
    render(<ConfirmDialog request={destructive} onApprove={vi.fn()} onReject={vi.fn()} />);
    // The path appears twice by design: once in the detail table and once
    // beside the acknowledgement, so it is unmissable either way.
    expect(screen.getAllByText('/home/me/notes.md')).toHaveLength(2);
    expect(screen.getByText('Recoverable')).toBeInTheDocument();
    expect(screen.getAllByText(/cannot be undone/i).length).toBeGreaterThan(0);
  });

  it('disables approval for a destructive action until it is acknowledged', async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<ConfirmDialog request={destructive} onApprove={onApprove} onReject={vi.fn()} />);

    const approve = screen.getByRole('button', { name: /yes, do it/i });
    expect(approve).toBeDisabled();

    await user.click(screen.getByRole('checkbox'));
    expect(approve).toBeEnabled();
    await user.click(approve);
    expect(onApprove).toHaveBeenCalledOnce();
  });

  it('does not require acknowledgement for a non-destructive action', () => {
    render(<ConfirmDialog request={benign} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByRole('button', { name: /approve/i })).toBeEnabled();
  });

  it('focuses the rejecting button so a stray Enter cannot approve', () => {
    render(<ConfirmDialog request={destructive} onApprove={vi.fn()} onReject={vi.fn()} />);
    expect(screen.getByRole('button', { name: /don't do it/i })).toHaveFocus();
  });

  it('rejects on Escape', async () => {
    const user = userEvent.setup();
    const onReject = vi.fn();
    render(<ConfirmDialog request={destructive} onApprove={vi.fn()} onReject={onReject} />);
    await user.keyboard('{Escape}');
    expect(onReject).toHaveBeenCalledOnce();
  });
});
