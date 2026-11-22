'use client';

export const dynamic = 'force-dynamic';

import * as React from 'react';
import { Cpu, HardDrive, ShieldCheck, Sparkles } from 'lucide-react';
import { Layout } from '@/components/layout';
import { Protected } from '@/components/protected';
import { PageHeader } from '@/components/ui/page';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ModelManager } from '@/components/koro/ModelManager';

export default function LocalModelsPage() {
  return (
    <Protected>
      <Layout>
        <div className="oa-page">
          <PageHeader
            title="Local models"
            description="Download and run an AI model on this machine. The assistant uses it to build workflows and agents — no account, cloud key or internet connection required."
            breadcrumbs={[
              { label: 'Home', href: '/' },
              { label: 'Settings', href: '/settings' },
              { label: 'Local models' },
            ]}
          />

          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Sparkles className="h-4 w-4 text-primary" />
                  Models on this machine
                </CardTitle>
                <CardDescription>
                  Runtimes are detected automatically. Ollama can download models from
                  here with live progress; other runtimes manage their own downloads.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <ModelManager />
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <ShieldCheck className="h-4 w-4 text-emerald-500" />
                    Fully local
                  </CardTitle>
                  <CardDescription>
                    Nothing leaves this machine unless you configure a hosted endpoint.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-2 text-sm text-muted-foreground">
                  <p>
                    The API server talks to a loopback runtime on 127.0.0.1. Prompts,
                    workflow drafts and model files stay on disk.
                  </p>
                  <p>
                    The assistant also works in command mode with no model installed, so
                    you can keep building even before a download finishes.
                  </p>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Cpu className="h-4 w-4 text-primary" />
                    Choosing a model
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-sm text-muted-foreground">
                  <p>
                    <strong className="text-foreground">0.5B–1.5B</strong> models are fast
                    on CPU and fine for short workflow commands.
                  </p>
                  <p>
                    <strong className="text-foreground">3B–7B</strong> models produce
                    better multi-step automations if you have 8–16 GB of RAM or a GPU.
                  </p>
                  <p className="flex items-start gap-2">
                    <HardDrive className="mt-0.5 h-4 w-4 shrink-0" />
                    Model files are a few hundred MB to several GB. Check free disk space
                    before downloading a large one.
                  </p>
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </Layout>
    </Protected>
  );
}
