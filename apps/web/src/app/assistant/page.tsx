'use client';

import { Layout } from '@/components/layout';
import { Protected } from '@/components/protected';
import { AssistantWorkspace } from '@/components/assistant/AssistantWorkspace';

export default function AssistantPage() {
  return (
    <Protected>
      <Layout>
        <AssistantWorkspace />
      </Layout>
    </Protected>
  );
}
