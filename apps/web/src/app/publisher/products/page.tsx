import { redirect } from 'next/navigation';
import { legacyTarget } from '@/features/navigation/surfaces';

// Phase 14: this page is now a chat tool. The URL stays valid and forwards to
// the assistant with the intent the page used to serve.
export default function LegacyRoute() {
  redirect(legacyTarget('/publisher/products'));
}
