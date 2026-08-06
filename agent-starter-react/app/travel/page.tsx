import { headers } from 'next/headers';
import { App } from '@/components/app/app';
import { getAppConfig } from '@/lib/utils';

export const metadata = {
  title: 'Thomas Cook · Travel Assistant (Tacy)',
  description: 'Speak with Tacy, your Thomas Cook AI Travel Expert',
};

export default async function TravelPage() {
  const hdrs = await headers();
  const appConfig = await getAppConfig(hdrs);

  // Override agentName for travel — baked in server-side before LiveKit initializes
  appConfig.agentName = 'tc-travel-bot';
  appConfig.startButtonText = 'Start Travel Call';

  return <App appConfig={appConfig} />;
}
