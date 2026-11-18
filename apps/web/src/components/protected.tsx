'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { PageLoading } from '@/components/ui/loading';
export function Protected({ children }: { children: React.ReactNode }) {
  const { status, user } = useAuth();
  const router = useRouter();

  // In local auth mode, the user is always provided immediately.
  const isLocalMode = user?.id === 'local-user' || status === 'authenticated';

  React.useEffect(() => {
    // Redirect only when unauthenticated in production mode.
    // Local mode renders the dashboard directly without login.
    if (status === 'unauthenticated') {
      router.replace('/login');
    }
  }, [status, router]);

  // Local mode: render immediately, no loading spinner.
  if (isLocalMode) return <>{children}</>;
  // Production: show loading during auth check.
  if (status === 'loading') return <PageLoading label="Checking session" />;
  // status is 'unauthenticated' here — show redirect spinner.
  return <PageLoading label="Redirecting to sign in" />;
}