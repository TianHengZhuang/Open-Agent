'use client';

import * as React from 'react';
import { api, ApiError } from '@/lib/api';

// Session state always comes from the API. In local mode the API can mint a
// loopback-only session without a password, so the app opens straight into the
// workspace; the login page stays available for real accounts.
const authMode = typeof process !== 'undefined' ? process.env.NEXT_PUBLIC_OPENAGENT_AUTH_MODE : undefined;
const isLocalAuthMode = authMode === 'local';
const LOCAL_SIGNOUT_KEY = 'oa:local-signed-out';
// Set after a password login so local auto-sign-in does not override the
// account the user explicitly chose.
const ACCOUNT_SESSION_KEY = 'oa:account-session';

function normalizeUser(raw: Partial<User>, fallbackEmail: string): User {
  return {
    id: String(raw.id ?? ''),
    email: String(raw.email ?? fallbackEmail),
    display_name: (raw.display_name as string | null) ?? null,
    avatar_url: (raw.avatar_url as string | null) ?? null,
    status: String(raw.status ?? 'active'),
    email_verified: Boolean(raw.email_verified),
    is_superadmin: Boolean(raw.is_superadmin),
    is_platform_owner: Boolean(raw.is_platform_owner),
    created_at: String(raw.created_at ?? new Date().toISOString()),
  };
}
function getInitialAuthState(): { status: 'loading' | 'authenticated' | 'unauthenticated'; user: User | null; isLoading: boolean; error: string | null } {
  return {
    status: 'loading',
    user: null,
    isLoading: true,
    error: null,
  };
}

export interface User {
  id: string;
  email: string;
  display_name: string | null;
  avatar_url: string | null;
  status: string;
  email_verified: boolean;
  is_superadmin: boolean;
  is_platform_owner: boolean;
  created_at: string;
}

export interface Session {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  current: boolean;
}

export interface RegisterResult {
  message: string;
  user_id: string;
  verification_sent: boolean;
  email_verified: boolean;
}

interface AuthState {
  status: 'loading' | 'authenticated' | 'unauthenticated';
  user: User | null;
  isLoading: boolean;
  error: string | null;
}

interface AuthContextType extends AuthState {
  login: (email: string, password: string, rememberMe?: boolean) => Promise<void>;
  register: (email: string, password: string, displayName?: string) => Promise<RegisterResult>;
  logout: (logoutAll?: boolean) => Promise<void>;
  verifyEmail: (token: string) => Promise<void>;
  resendVerification: (email: string) => Promise<string>;
  forgotPassword: (email: string) => Promise<void>;
  resetPassword: (token: string, password: string) => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  fetchUser: () => Promise<void>;
  fetchSessions: () => Promise<Session[]>;
  revokeSession: (sessionId: string) => Promise<void>;
  clearError: () => void;
  sessions?: Session[];
}

const AuthContext = React.createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<AuthState>(getInitialAuthState);
  const [sessions, setSessions] = React.useState<Session[]>([]);

  const clearError = React.useCallback(() => {
    setState(prev => ({ ...prev, error: null }));
  }, []);

  const setError = React.useCallback((error: string | null) => {
    setState(prev => ({ ...prev, error }));
  }, []);

  const setUser = React.useCallback((user: User | null) => {
    setState(prev => ({
      ...prev,
      user,
      status: user ? 'authenticated' : 'unauthenticated',
      isLoading: false,
    }));
  }, []);

  const fetchUser = React.useCallback(async () => {
    try {
      if (isLocalAuthMode) {
        let signedOut = false;
        let usingAccount = false;
        try {
          signedOut = window.localStorage.getItem(LOCAL_SIGNOUT_KEY) === '1';
          usingAccount = window.localStorage.getItem(ACCOUNT_SESSION_KEY) === '1';
        } catch {
          /* storage unavailable */
        }
        if (!signedOut && !usingAccount) {
          try {
            const local = await api.post<{ user: Partial<User> }>('/auth/local-session');
            if (local?.user?.id) {
              setUser(normalizeUser(local.user, 'local@openagent'));
              return;
            }
          } catch {
            // Local mode disabled on the API — fall back to a real session.
          }
        }
      }

      setState(prev => ({ ...prev, isLoading: true }));
      const data = await api.get<User | { user: User }>('/auth/me');
      const user = (data as { user?: User }).user ?? (data as User);
      if (user && (user as User).id) setUser(user as User);
      else setUser(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setUser(null);
      } else {
        setUser(null);
        console.error('Failed to fetch user:', error);
      }
    }
  }, [setUser]);

  const login = React.useCallback(async (email: string, password: string, rememberMe = false) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      const data = await api.post<{ user: User; session_token: string; expires_at: string }>('/auth/login', {
        email,
        password,
        remember_me: rememberMe,
      });

      // The session cookie is set by the server; normalize user shape.
      try {
        window.localStorage.removeItem(LOCAL_SIGNOUT_KEY);
        window.localStorage.setItem(ACCOUNT_SESSION_KEY, '1');
      } catch {
        /* storage unavailable */
      }
      setUser(normalizeUser(data.user as Partial<User>, email));
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setUser, setError]);

  const register = React.useCallback(async (email: string, password: string, displayName?: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      const data = await api.post<RegisterResult>('/auth/register', {
        email,
        password,
        display_name: displayName,
      });

      setState(prev => ({ ...prev, isLoading: false }));
      return data;
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const resendVerification = React.useCallback(async (email: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      const data = await api.post<{ message: string }>('/auth/resend-verification', { email });
      setState(prev => ({ ...prev, isLoading: false }));
      return data.message;
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const logout = React.useCallback(async (logoutAll = false) => {
    try {
      setState(prev => ({ ...prev, isLoading: true }));
      await api.post('/auth/logout', { logout_all: logoutAll });
      try {
        window.localStorage.removeItem(ACCOUNT_SESSION_KEY);
        if (isLocalAuthMode) window.localStorage.setItem(LOCAL_SIGNOUT_KEY, '1');
      } catch {
        /* storage unavailable */
      }
      setUser(null);
    } catch (error) {
      setUser(null);
      console.error('Logout failed:', error);
    }
  }, [setUser]);

  const verifyEmail = React.useCallback(async (token: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      await api.post('/auth/verify-email', { token });
      setState(prev => ({ ...prev, isLoading: false }));
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const forgotPassword = React.useCallback(async (email: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      await api.post('/auth/forgot-password', { email });
      setState(prev => ({ ...prev, isLoading: false }));
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const resetPassword = React.useCallback(async (token: string, password: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      await api.post('/auth/reset-password', { token, password });
      setState(prev => ({ ...prev, isLoading: false }));
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const changePassword = React.useCallback(async (currentPassword: string, newPassword: string) => {
    try {
      setState(prev => ({ ...prev, isLoading: true, error: null }));
      await api.post('/auth/change-password', { current_password: currentPassword, new_password: newPassword });
      setState(prev => ({ ...prev, isLoading: false }));
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  const fetchSessions = React.useCallback(async () => {
    try {
      const data = await api.get<{ sessions: Session[] }>('/auth/sessions');
      setSessions(data.sessions);
      return data.sessions;
    } catch (error) {
      console.error('Failed to fetch sessions:', error);
      return [];
    }
  }, []);

  const revokeSession = React.useCallback(async (sessionId: string) => {
    try {
      await api.delete(`/auth/sessions/${sessionId}`);
      setSessions(prev => prev.filter(s => s.id !== sessionId));
    } catch (error) {
      if (error instanceof ApiError) {
        setError(error.message);
        throw error;
      }
      throw error;
    }
  }, [setError]);

  // Initialize auth on mount
  React.useEffect(() => {
    fetchUser();
    // The effect runs once on mount. In local mode, fetchUser is a no-op
    // (sets placeholder user without API call), so there's no state change
    // that would trigger a re-run. In production mode, the API call result
    // may change state, but the deps [fetchUser] are stable (setUser has [] deps),
    // so the effect won't re-run unnecessarily.
  }, [fetchUser]);

  return (
    <AuthContext.Provider value={{
      ...state,
      login,
      register,
      logout,
      verifyEmail,
      resendVerification,
      forgotPassword,
      resetPassword,
      changePassword,
      fetchUser,
      fetchSessions,
      revokeSession,
      clearError,
      sessions,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = React.useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
