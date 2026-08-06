import { headers } from 'next/headers';
import { App } from '@/components/app/app';
import { getAppConfig } from '@/lib/utils';

export const metadata = {
  title: 'Thomas Cook · Forex Assistant (Priya)',
  description: 'Speak with Priya, your Thomas Cook AI Forex Specialist',
};

export default async function ForexPage() {
  const hdrs = await headers();
  const appConfig = await getAppConfig(hdrs);

  // Override agentName for forex — baked in server-side before LiveKit initializes
  appConfig.agentName = 'tc-forex-bot';
  appConfig.startButtonText = 'Start Forex Call';

  return <App appConfig={appConfig} />;
}
