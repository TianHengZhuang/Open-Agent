import { describe, expect, it } from 'vitest';
import { curlToMutation, parseCurl } from './curl';

describe('parseCurl', () => {
  it('parses a POST with headers and a JSON body', () => {
    const request = parseCurl(
      `curl -X POST 'https://api.example.com/tickets' \\
        -H 'Content-Type: application/json' \\
        -H "Authorization: Bearer abc" \\
        -d '{"title":"Hi"}'`,
    );
    expect(request).toEqual({
      method: 'POST',
      url: 'https://api.example.com/tickets',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer abc' },
      body: '{"title":"Hi"}',
    });
  });

  it('defaults to GET and reads --url / -b', () => {
    const request = parseCurl('curl --url https://example.com/feed -b "session=1"');
    expect(request?.method).toBe('GET');
    expect(request?.url).toBe('https://example.com/feed');
    expect(request?.headers.Cookie).toBe('session=1');
    expect(request?.body).toBeUndefined();
  });

  it('ignores unknown flags and rejects non-curl input', () => {
    expect(parseCurl('wget https://example.com')).toBeNull();
    expect(parseCurl('curl')).toBeNull();
    expect(parseCurl('curl --compressed https://example.com')?.url).toBe('https://example.com');
  });
});

describe('curlToMutation', () => {
  it('builds an add_node mutation for the HTTP Request node', () => {
    const mutation = curlToMutation(
      "curl -X PATCH https://api.example.com/orders/1 -d '{\"status\":\"paid\"}'",
      { id: 'n_http', position: { x: 300, y: 90 } },
    );

    expect(mutation).toMatchObject({
      op: 'add_node',
      id: 'n_http',
      type: 'webhook',
      position: { x: 300, y: 90 },
      config: {
        url: 'https://api.example.com/orders/1',
        method: 'PATCH',
        body: '{"status":"paid"}',
      },
    });
    expect(mutation?.label).toBe('HTTP PATCH api.example.com');
  });

  it('returns null for invalid input', () => {
    expect(curlToMutation('not a curl command')).toBeNull();
  });
});
