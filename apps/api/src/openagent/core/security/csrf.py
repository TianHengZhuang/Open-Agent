import secrets
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from openagent.core.config import get_settings


class CSRFProtection:
    """CSRF protection using double-submit cookie pattern."""
    
    def __init__(self):
        self.settings = get_settings()
        self.cookie_name = "csrf_token"
        self.header_name = "X-CSRF-Token"
        self.token_length = 32
    
    def generate_token(self) -> str:
        """Generate a new CSRF token."""
        return secrets.token_urlsafe(self.token_length)
    
    def get_token_from_request(self, request: Request) -> Optional[str]:
        """Get CSRF token from request (header or cookie)."""
        # Check header first
        token = request.headers.get(self.header_name)
        if token:
            return token
        
        # Check cookie
        return request.cookies.get(self.cookie_name)
    
    def validate_token(self, request: Request, form_token: Optional[str] = None) -> bool:
        """Validate CSRF token from request."""
        # For API requests, check header
        if request.headers.get("Content-Type", "").startswith("application/json"):
            token = request.headers.get(self.header_name)
            cookie_token = request.cookies.get(self.cookie_name)
            
            if not token or not cookie_token:
                return False
            
            # Compare tokens (constant-time comparison)
            return secrets.compare_digest(token, cookie_token)
        
        # For form submissions, check form field
        if form_token:
            cookie_token = request.cookies.get(self.cookie_name)
            if not form_token or not cookie_token:
                return False
            return secrets.compare_digest(form_token, cookie_token)
        
        return False
    
    def set_csrf_cookie(self, response: Response, token: str) -> None:
        """Set CSRF token cookie."""
        response.set_cookie(
            key=self.cookie_name,
            value=token,
            max_age=3600 * 24 * 7,  # 7 days
            httponly=False,  # Must be readable by JavaScript for double-submit
            secure=self.settings.SESSION_COOKIE_SECURE,
            samesite=self.settings.SESSION_COOKIE_SAME_SITE,
            domain=self.settings.SESSION_COOKIE_DOMAIN or None,
            path="/",
        )
    
    def get_or_create_token(self, request: Request, response: Response) -> str:
        """Get existing CSRF token or create new one."""
        token = request.cookies.get(self.cookie_name)
        if not token:
            token = self.generate_token()
            self.set_csrf_cookie(response, token)
        return token


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection middleware."""
    
    def __init__(self, app: ASGIApp, exempt_paths: Optional[list[str]] = None):
        super().__init__(app)
        self.csrf = CSRFProtection()
        self.exempt_paths = exempt_paths or [
            "/api/v1/health",
            "/api/v1/health/ready",
            "/api/v1/auth/local-session",
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/auth/verify-email",
            "/api/v1/auth/forgot-password",
            "/api/v1/auth/reset-password",
            "/docs",
            "/redoc",
            "/openapi.json",
        ]
    
    def _is_exempt(self, path: str) -> bool:
        """Check if path is exempt from CSRF protection."""
        for exempt in self.exempt_paths:
            if path.startswith(exempt):
                return True
        return False
    
    def _is_safe_method(self, method: str) -> bool:
        """Check if HTTP method is safe (doesn't modify state)."""
        return method in ("GET", "HEAD", "OPTIONS", "TRACE")
    
    async def dispatch(self, request: Request, call_next):
        # Skip CSRF for exempt paths
        if self._is_exempt(request.url.path):
            return await call_next(request)

        settings = get_settings()
        uses_session_cookie = bool(request.cookies.get(settings.SESSION_COOKIE_NAME))

        # Safe methods cannot change state. Ensure a double-submit cookie exists
        # so the browser client can echo it on the next mutation.
        if self._is_safe_method(request.method):
            response = await call_next(request)
            if not request.cookies.get(self.csrf.cookie_name):
                self.csrf.set_csrf_cookie(response, self.csrf.generate_token())
            return response

        # Non-cookie auth (Bearer/API key) is not vulnerable to CSRF, and
        # unauthenticated mutations carry no ambient authority to abuse.
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and len(auth_header) > 20:
            return await call_next(request)
        if not uses_session_cookie:
            return await call_next(request)
        # Browsers send Origin/Referer on cross-site mutations. Non-browser API
        # clients with a cookie but no Origin are not a CSRF vector.
        if not (request.headers.get("origin") or request.headers.get("referer")):
            return await call_next(request)

        # Validate the double-submit token for cookie-authenticated mutations.
        if not self.csrf.validate_token(request):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "CSRF token validation failed",
                    "code": "CSRF_VALIDATION_FAILED",
                },
            )

        response = await call_next(request)
        if not request.cookies.get(self.csrf.cookie_name):
            self.csrf.set_csrf_cookie(response, self.csrf.generate_token())
        return response


def add_csrf_token_to_response(request: Request, response: Response) -> None:
    """Add CSRF token to response if not present."""
    csrf = CSRFProtection()
    csrf.get_or_create_token(request, response)