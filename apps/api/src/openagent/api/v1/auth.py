from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openagent.core.config import get_settings
from openagent.core.security import create_token_pair, hash_token
from openagent.core.security.rate_limit import check_auth_rate_limit
from openagent.db.models import Session, User, UserStatus
from openagent.db.session import get_db
from openagent.schemas.base import ApiErrorResponse
from openagent.services.auth import (
    AccountNotVerifiedError,
    AccountSuspendedError,
    AuthenticationError,
    AuthenticationService,
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    PasswordPolicyError,
    TokenExpiredError,
    TokenInvalidError,
)
from openagent.services.workspaces import ensure_personal_workspace

logger = structlog.get_logger("openagent.auth")

router = APIRouter(prefix="/auth", tags=["authentication"])

# Security scheme for API docs
security = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=255)


class RegisterResponse(BaseModel):
    message: str
    user_id: str
    verification_sent: bool
    email_verified: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    remember_me: bool = False


class LoginResponse(BaseModel):
    user: dict
    session_token: str
    expires_at: datetime


class VerifyEmailRequest(BaseModel):
    token: str


class VerifyEmailResponse(BaseModel):
    message: str


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ResendVerificationResponse(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=128)


class ResetPasswordResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=128)


class ChangePasswordResponse(BaseModel):
    message: str


class LogoutRequest(BaseModel):
    logout_all: bool = False


class LogoutResponse(BaseModel):
    message: str


class SessionResponse(BaseModel):
    id: str
    user_agent: Optional[str]
    ip_address: Optional[str]
    created_at: datetime
    last_seen_at: Optional[datetime]
    expires_at: datetime
    current: bool


class SessionsResponse(BaseModel):
    sessions: list[SessionResponse]


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str]
    avatar_url: Optional[str]
    status: str
    email_verified: bool
    is_superadmin: bool
    is_platform_owner: bool
    created_at: datetime


def set_session_cookie(response: Response, session_token: str, expires_at: datetime, remember_me: bool = False) -> None:
    """Set session cookie on response."""
    settings = get_settings()
    
    max_age = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if max_age < 0:
        max_age = 0
    
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=settings.SESSION_COOKIE_HTTP_ONLY,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAME_SITE,
        domain=settings.SESSION_COOKIE_DOMAIN or None,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie."""
    settings = get_settings()
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        domain=settings.SESSION_COOKIE_DOMAIN or None,
        path="/",
    )


async def get_current_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[Session]:
    """Get current session from cookie."""
    settings = get_settings()
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    
    if not session_token:
        return None
    
    auth_service = AuthenticationService(db)
    return await auth_service.get_session(session_token)


async def get_current_user(
    session: Optional[Session] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user."""
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Not authenticated", "code": "UNAUTHORIZED"},
        )
    
    # Get user
    result = await db.execute(
        select(User).where(User.id == session.user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "User not found", "code": "USER_NOT_FOUND"},
        )
    
    # Check account status
    if user.status in (UserStatus.SUSPENDED, UserStatus.INACTIVE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Account suspended", "code": "ACCOUNT_SUSPENDED"},
        )
    
    return user


async def get_current_platform_owner(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current user and verify platform owner status."""
    auth_service = AuthenticationService(db)
    is_owner = await auth_service.is_platform_owner(user.id)
    
    if not is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Platform owner access required", "code": "FORBIDDEN"},
        )
    
    return user


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ApiErrorResponse},
        409: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def register(
    request: Request,
    response: Response,
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user."""
    # Rate limiting
    await check_auth_rate_limit(request, "register", data.email)
    
    auth_service = AuthenticationService(db)
    
    try:
        user, verification_token = await auth_service.register(
            email=data.email,
            password=data.password,
            display_name=data.display_name,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
        
        await db.commit()
        
        if not auth_service.email_delivers:
            # No mail channel is configured (development or SMTP-less
            # self-hosting): the emailed verification link can never arrive,
            # so complete verification now — otherwise every new account
            # would be stuck unable to sign in.
            user.email_verified = True
            user.status = UserStatus.ACTIVE
            await db.commit()

        # Every user needs an organization: the dashboard, workflows and runs
        # are all org-scoped, so an account without one can sign in but can
        # never do anything. Provision a personal workspace on first signup.
        await ensure_personal_workspace(db, user)
        await db.commit()

        email_verified = user.email_verified
        return RegisterResponse(
            message=(
                "Registration successful. Please check your email to verify your account."
                if not email_verified
                else "Registration successful. You can now sign in."
            ),
            user_id=str(user.id),
            verification_sent=not email_verified,
            email_verified=email_verified,
        )
    
    except EmailAlreadyExistsError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "An account with this email already exists", "code": "EMAIL_ALREADY_EXISTS"},
        )
    except PasswordPolicyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(e), "code": "PASSWORD_POLICY_VIOLATION"},
        )
    except AuthenticationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "code": e.code},
        )


LOCAL_USER_EMAIL = "local@openagent"


class LocalSessionResponse(BaseModel):
    user: dict
    session_token: str
    expires_at: datetime


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


@router.post("/local-session", response_model=LocalSessionResponse)
async def local_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Sign the local operator in without a password.

    Only enabled with ``OPENAGENT_AUTH_MODE=local``, never in production, and
    only for loopback clients. Organization, permissions and sessions behave
    exactly like a normal login — this replaces the password prompt, not auth.
    """
    settings = get_settings()
    if settings.OPENAGENT_AUTH_MODE.lower() != "local" or settings.OPENAGENT_ENV == "production":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Local mode is not enabled", "code": "LOCAL_MODE_DISABLED"},
        )
    if not _is_loopback(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "Local sign-in is only available from this computer",
                "code": "LOCAL_MODE_LOOPBACK_ONLY",
            },
        )

    result = await db.execute(select(User).where(User.email == LOCAL_USER_EMAIL))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=LOCAL_USER_EMAIL,
            display_name="Local User",
            email_verified=True,
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        await db.flush()
    else:
        user.status = UserStatus.ACTIVE
        user.email_verified = True

    await ensure_personal_workspace(db, user, seed_starter=True)

    token, token_hash, expires_at = create_token_pair(60 * 24 * 30)
    session = Session(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=request.headers.get("User-Agent"),
        ip_address=request.client.host if request.client else None,
        last_seen_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()
    set_session_cookie(response, token, expires_at, remember_me=True)

    return LocalSessionResponse(
        user={
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "status": user.status.value if hasattr(user.status, "value") else str(user.status),
            "email_verified": user.email_verified,
            "is_superadmin": user.is_superadmin,
            "is_platform_owner": False,
            "created_at": user.created_at.isoformat() if user.created_at else datetime.now(timezone.utc).isoformat(),
        },
        session_token=token,
        expires_at=expires_at,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        400: {"model": ApiErrorResponse},
        401: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def login(
    request: Request,
    response: Response,
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Login user."""
    # Rate limiting
    await check_auth_rate_limit(request, "login", data.email)
    
    auth_service = AuthenticationService(db)
    
    try:
        user, session, session_token = await auth_service.login(
            email=data.email,
            password=data.password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
        
        await db.commit()
        
        # Set session cookie
        set_session_cookie(response, session_token, session.expires_at, data.remember_me)
        
        return LoginResponse(
            user={
                "id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "avatar_url": user.avatar_url,
                "status": user.status.value,
                "email_verified": user.email_verified,
                "is_superadmin": user.is_superadmin,
            },
            session_token=session_token,
            expires_at=session.expires_at,
        )
    
    except InvalidCredentialsError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Invalid email or password", "code": "INVALID_CREDENTIALS"},
        )
    except AccountNotVerifiedError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Please verify your email address before logging in", "code": "ACCOUNT_NOT_VERIFIED"},
        )
    except AccountSuspendedError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "Your account has been suspended", "code": "ACCOUNT_SUSPENDED"},
        )
    except AuthenticationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "code": e.code},
        )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={
        401: {"model": ApiErrorResponse},
    },
)
async def logout(
    request: Request,
    response: Response,
    data: LogoutRequest = LogoutRequest(),
    session: Optional[Session] = Depends(get_current_session),
    db: AsyncSession = Depends(get_db),
):
    """Logout current session or all sessions."""
    if not session:
        # Still clear cookie
        clear_session_cookie(response)
        return LogoutResponse(message="Logged out")
    
    auth_service = AuthenticationService(db)
    
    if data.logout_all:
        await auth_service.logout_all(
            session.user_id,
            except_session_id=None,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
    else:
        await auth_service.logout(
            session,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
    
    await db.commit()
    clear_session_cookie(response)
    
    return LogoutResponse(message="Logged out successfully")


@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    responses={
        400: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def verify_email(
    request: Request,
    data: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address with token."""
    await check_auth_rate_limit(request, "verify_email")
    
    auth_service = AuthenticationService(db)
    
    try:
        await auth_service.verify_email(
            token=data.token,
            request_id=getattr(request.state, "request_id", None),
        )
        
        await db.commit()
        
        return VerifyEmailResponse(message="Email verified successfully")
    
    except TokenExpiredError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Verification link has expired", "code": "TOKEN_EXPIRED"},
        )
    except TokenInvalidError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid or already used verification link", "code": "TOKEN_INVALID"},
        )
    except AuthenticationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "code": e.code},
        )


@router.post(
    "/resend-verification",
    response_model=ResendVerificationResponse,
    responses={
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def resend_verification(
    request: Request,
    data: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Re-send the email verification link for a pending account."""
    await check_auth_rate_limit(request, "resend_verification", data.email)

    auth_service = AuthenticationService(db)

    await auth_service.resend_verification(
        email=data.email,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )
    await db.commit()

    # Always the same response so this endpoint cannot be used to probe
    # which email addresses have accounts.
    return ResendVerificationResponse(
        message="If an account exists and still needs verification, a new link has been sent."
    )


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    responses={
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def forgot_password(
    request: Request,
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Request password reset."""
    await check_auth_rate_limit(request, "forgot_password", data.email)
    
    auth_service = AuthenticationService(db)
    
    await auth_service.request_password_reset(
        email=data.email,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )
    
    await db.commit()
    
    # Always return success to prevent enumeration
    return ForgotPasswordResponse(
        message="If an account exists with this email, a password reset link has been sent."
    )


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    responses={
        400: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def reset_password(
    request: Request,
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password with token."""
    await check_auth_rate_limit(request, "reset_password")
    
    auth_service = AuthenticationService(db)
    
    try:
        await auth_service.reset_password(
            token=data.token,
            new_password=data.password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
        
        await db.commit()
        
        return ResetPasswordResponse(message="Password reset successfully")
    
    except TokenExpiredError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Reset link has expired", "code": "TOKEN_EXPIRED"},
        )
    except TokenInvalidError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid or already used reset link", "code": "TOKEN_INVALID"},
        )
    except PasswordPolicyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(e), "code": "PASSWORD_POLICY_VIOLATION"},
        )
    except AuthenticationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "code": e.code},
        )


@router.post(
    "/change-password",
    response_model=ChangePasswordResponse,
    responses={
        400: {"model": ApiErrorResponse},
        401: {"model": ApiErrorResponse},
        403: {"model": ApiErrorResponse},
        422: {"model": ApiErrorResponse},
        429: {"model": ApiErrorResponse},
    },
)
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change password for authenticated user."""
    await check_auth_rate_limit(request, "change_password", user.email)
    
    auth_service = AuthenticationService(db)
    
    try:
        await auth_service.change_password(
            user_id=user.id,
            current_password=data.current_password,
            new_password=data.new_password,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent"),
            request_id=getattr(request.state, "request_id", None),
        )
        
        await db.commit()
        
        return ChangePasswordResponse(message="Password changed successfully")
    
    except InvalidCredentialsError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Current password is incorrect", "code": "INVALID_CREDENTIALS"},
        )
    except PasswordPolicyError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": str(e), "code": "PASSWORD_POLICY_VIOLATION"},
        )
    except AuthenticationError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": e.message, "code": e.code},
        )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    responses={
        401: {"model": ApiErrorResponse},
    },
)
async def get_current_user_info(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current authenticated user information."""
    auth_service = AuthenticationService(db)
    is_platform_owner = await auth_service.is_platform_owner(user.id)
    
    return CurrentUserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        status=user.status.value,
        email_verified=user.email_verified,
        is_superadmin=user.is_superadmin,
        is_platform_owner=is_platform_owner,
        created_at=user.created_at,
    )


@router.get(
    "/sessions",
    response_model=SessionsResponse,
    responses={
        401: {"model": ApiErrorResponse},
    },
)
async def get_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all active sessions for current user."""
    auth_service = AuthenticationService(db)
    sessions = await auth_service.get_user_sessions(user.id)
    
    # Get current session token from cookie
    settings = get_settings()
    current_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    current_token_hash = hash_token(current_token) if current_token else None
    
    session_responses = []
    for session in sessions:
        session_responses.append(SessionResponse(
            id=str(session.id),
            user_agent=session.user_agent,
            ip_address=session.ip_address,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            current=session.token_hash == current_token_hash,
        ))
    
    return SessionsResponse(sessions=session_responses)


@router.delete(
    "/sessions/{session_id}",
    response_model=LogoutResponse,
    responses={
        401: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    },
)
async def revoke_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a specific session."""
    from uuid import UUID
    
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Invalid session ID", "code": "INVALID_SESSION_ID"},
        )
    
    auth_service = AuthenticationService(db)
    success = await auth_service.revoke_session(
        session_id=session_uuid,
        user_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
        request_id=getattr(request.state, "request_id", None),
    )
    
    await db.commit()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Session not found", "code": "SESSION_NOT_FOUND"},
        )
    
    return LogoutResponse(message="Session revoked")