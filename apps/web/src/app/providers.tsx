'use client';

import { ReactNode } from 'react';
import { QueryProvider } from '@/lib/query-provider';
import { ThemeProvider } from 'next-themes';
import { AuthProvider } from '@/context/AuthContext';
import { OrganizationProvider } from '@/context/OrganizationContext';
import { FeatureFlagProvider } from '@/lib/feature-flags';
import { ToastProvider } from '@/components/ui/toast';
import { KoroProvider } from '@/context/KoroContext';
import { GlobalKoroDock } from '@/components/koro/GlobalKoroDock';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryProvider>
        <AuthProvider>
          <OrganizationProvider>              <FeatureFlagProvider>
                <ToastProvider>
                  <KoroProvider>
                    {children}
                    <GlobalKoroDock />
                  </KoroProvider>
                </ToastProvider>
              </FeatureFlagProvider>
          </OrganizationProvider>
        </AuthProvider>
      </QueryProvider>
    </ThemeProvider>
  );
}
