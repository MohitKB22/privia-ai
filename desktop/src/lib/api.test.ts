import { describe, expect, it, vi } from 'vitest';
import { ApiRequestError, api } from './api';

/** A fetch stub that builds a fresh Response per call: bodies are single-use. */
function stubFetch(factory: () => Response) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(() => Promise.resolve(factory())),
  );
}

describe('api client', () => {
  it('turns an error envelope into a typed error', async () => {
    stubFetch(
      () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'TOOL_PERMISSION_DENIED',
              message: 'PRIVIA needs your permission for: files:read',
              request_id: 'req_1',
              details: { missing_scopes: ['files:read'] },
            },
          }),
          { status: 403 },
        ),
    );

    try {
      await api.status();
      throw new Error('the request should have failed');
    } catch (caught) {
      expect(caught).toBeInstanceOf(ApiRequestError);
      const error = caught as ApiRequestError;
      expect(error.code).toBe('TOOL_PERMISSION_DENIED');
      expect(error.isPermission).toBe(true);
      expect(error.status).toBe(403);
      expect(error.requestId).toBe('req_1');
      expect(error.details.missing_scopes).toEqual(['files:read']);
    }
  });

  it('reports an unreachable backend distinctly from a server error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    try {
      await api.health();
      throw new Error('the request should have failed');
    } catch (caught) {
      const error = caught as ApiRequestError;
      expect(error.isOffline).toBe(true);
      expect(error.isPermission).toBe(false);
      expect(error.message).toMatch(/backend is not running/i);
    }
  });

  it('falls back to a generic code when the body is not an envelope', async () => {
    stubFetch(() => new Response('<html>gateway error</html>', { status: 502 }));
    try {
      await api.status();
      throw new Error('the request should have failed');
    } catch (caught) {
      const error = caught as ApiRequestError;
      expect(error.code).toBe('INTERNAL_ERROR');
      expect(error.status).toBe(502);
    }
  });

  it('parses a successful response', async () => {
    stubFetch(
      () =>
        new Response(JSON.stringify({ status: 'ok', version: '1.0.0', checks: {} }), {
          status: 200,
        }),
    );
    await expect(api.health()).resolves.toMatchObject({ status: 'ok', version: '1.0.0' });
  });
});
