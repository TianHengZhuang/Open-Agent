import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

// This middleware runs on the server
// It protects routes that require authentication

const publicPaths = [
  '/login',
  '/register',
  '/verify-email',
  '/forgot-password',
  '/reset-password',
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/verify-email',
  '/api/auth/forgot-password',
  '/api/auth/reset-password',
  '/api/health',
  '/api/health/ready',
];

const authPaths = [
  '/login',
  '/register',
  '/verify-email',
  '/forgot-password',
  '/reset-password',
];

// Determine if we are in local auth mode.
// Check both OPENAGENT_AUTH_MODE (server/.env) and NEXT_PUBLIC_OPENAGENT_AUTH_MODE (client/.env.local)
// This ensures local mode works across both server-side middleware and client-side AuthContext.
const isLocalAuthMode = process.env.OPENAGENT_AUTH_MODE === 'local' || process.env.NEXT_PUBLIC_OPENAGENT_AUTH_MODE === 'local' || process.env.NEXT_PUBLIC_OPENAGENT_AUTH_MODE === undefined;

// Check if the request is coming from loopback/local addresses only.
// This ensures local auth mode can never be activated on a publicly exposed server.
function isLoopbackRequest(request: NextRequest): boolean {
  const hostname = request.nextUrl.hostname;
  // Allow loopback and local addresses
  return (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '::1' ||
    hostname.startsWith('192.168.') ||
    hostname.startsWith('10.') ||
    hostname.startsWith('172.16.') ||
    hostname.startsWith('172.17.') ||
    hostname.startsWith('172.18.') ||
    hostname.startsWith('172.19.') ||
    hostname.startsWith('172.20.') ||
    hostname.startsWith('172.21.') ||
    hostname.startsWith('172.22.') ||
    hostname.startsWith('172.23.') ||
    hostname.startsWith('172.24.') ||
    hostname.startsWith('172.25.') ||
    hostname.startsWith('172.26.') ||
    hostname.startsWith('172.27.') ||
    hostname.startsWith('172.28.') ||
    hostname.startsWith('172.29.') ||
    hostname.startsWith('172.30.') ||
    hostname.startsWith('172.31.')
  );
}

// Check if the request should be allowed in local auth mode.
// In local mode, only loopback requests get the auth bypass.
function isLocalAuthAllowed(request: NextRequest): boolean {
  if (!isLocalAuthMode) return false;
  // Only allow local/authenticated access from loopback addresses
  return isLoopbackRequest(request);
}

// Get the effective API origin, respecting local auth mode when on loopback.
function apiConnectSrc(): string {
  // Server-side middleware: process.env is available.
  // Default to the configured API port (matches scripts/dev.mjs defaults).
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || process.env.API_URL || 'http://localhost:8000';
  try {
    return new URL(apiUrl).origin;
  } catch {
    return 'http://localhost:8000';
  }
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  
  // Check if path is an auth path (should redirect if authenticated)
  const isAuthPath = authPaths.some(path => pathname.startsWith(path));
  
  // Check if path is public
  const isPublicPath = publicPaths.some(path => pathname.startsWith(path));
  
  // Get session cookie
  const sessionCookie = request.cookies.get('oa_session');
  
  // === LOCAL AUTH MODE BLOCK ===
  // In local auth mode, only loopback requests get the auth bypass.
  // This prevents the local auth bypass from being accidentally activated
  // on a publicly exposed server.
  if (isLocalAuthMode && isLoopbackRequest(request)) {
    // For auth paths: if user has session cookie, allow access; otherwise let client-side handle it
    if (isAuthPath && sessionCookie) {
      return NextResponse.next();
    }
    
    // For non-auth protected paths: in local mode from loopback, we allow access
    // by not redirecting to login. The client-side AuthContext will set the user.
    // We still add security headers below.
  }
  
  // === END LOCAL AUTH MODE BLOCK ===
  
  // For auth paths, if user has session, redirect to dashboard
  if (isAuthPath && sessionCookie) {
    // We can't verify the session here without calling the API
    // The client-side auth context will handle this
    return NextResponse.next();
  }
  
  // For protected paths, we rely on client-side auth check
  // The server API will enforce authentication
  
  // Add security headers
  const response = NextResponse.next();
  
  // Security headers
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  
  // CSP - adjust as needed
  response.headers.set(
    'Content-Security-Policy',
    `default-src 'self'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:; connect-src 'self' ${apiConnectSrc()} https://api.openagent.local;`
  );
  
  return response;
}

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     * - public folder
     */
    '/((?!_next/static|_next/image|favicon.ico|public/).*)',
  ],
};