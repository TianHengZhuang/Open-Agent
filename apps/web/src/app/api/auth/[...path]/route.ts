import { NextRequest, NextResponse } from 'next/server';

const configuredApiBase = (
  process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1'
).replace(/\/+$/, '');
const API_BASE = configuredApiBase.endsWith('/api/v1')
  ? configuredApiBase
  : `${configuredApiBase}/api/v1`;

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, await context.params, 'GET');
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, await context.params, 'POST');
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, await context.params, 'PUT');
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, await context.params, 'PATCH');
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxyRequest(request, await context.params, 'DELETE');
}

async function proxyRequest(
  request: NextRequest,
  params: { path: string[] },
  method: string
) {
  const path = params.path.join('/');
  const url = `${API_BASE}/auth/${path}`;
  
  // Get search params
  const searchParams = request.nextUrl.searchParams.toString();
  const fullUrl = searchParams ? `${url}?${searchParams}` : url;
  
  // Forward headers (except host)
  const headers: HeadersInit = {};
  request.headers.forEach((value, key) => {
    if (key.toLowerCase() !== 'host') {
      headers[key] = value;
    }
  });
  
  // Ensure content-type for JSON requests
  if (method !== 'GET' && method !== 'DELETE') {
    headers['Content-Type'] = 'application/json';
  }
  
  // Get body for non-GET requests
  let body: string | undefined;
  if (method !== 'GET' && method !== 'DELETE') {
    try {
      body = await request.text();
    } catch {
      // No body
    }
  }
  
  try {
    const response = await fetch(fullUrl, {
      method,
      headers,
      body,
      // Don't forward cookies automatically - we'll handle session cookie manually
      credentials: 'omit',
    });
    
    // Get response data
    const data = await response.json().catch(() => ({}));
    
    // Create NextResponse
    const nextResponse = NextResponse.json(data, { status: response.status });
    
    // Forward set-cookie headers
    response.headers.getSetCookie().forEach(cookie => {
      // Parse cookie to preserve attributes
      nextResponse.headers.append('Set-Cookie', cookie);
    });
    
    return nextResponse;
  } catch (error) {
    console.error('API proxy error:', error);
    return NextResponse.json(
      { error: { message: 'Failed to connect to API', code: 'API_UNAVAILABLE' } },
      { status: 503 }
    );
  }
}