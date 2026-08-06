'use client';

import React from 'react';
import { Room } from 'livekit-client';
import { LiveKitRoom } from '@livekit/components-react';
import { FareCalendarCard } from '@/components/custom-cards/fare-calendar-card';
import { PackageList } from '@/components/custom-cards/package-list';
import { PricingCard } from '@/components/custom-cards/pricing-card';
import fareCalendarJson from './get_fare_calendar.json';
import packagePricingFalseJson from './get_package_pricing_flight_false.json';
import packagePricingJson from './get_package_pricing_flight_true.json';
import travelPackageJson from './get_travel_package.json';

const dummyRoom = new Room();

export default function TestPackagesPage() {
  return (
    <LiveKitRoom room={dummyRoom} serverUrl="wss://dummy.livekit.cloud" token="dummy-token">
      <div className="min-h-screen bg-zinc-50 p-8">
        <div className="mx-auto max-w-4xl space-y-12">
          <section>
            <h2 className="mb-6 text-2xl font-bold">Package List (PackageCard inside)</h2>
            <div className="flex justify-center">
              <PackageList data={travelPackageJson.body} />
            </div>
          </section>

          <section>
            <h2 className="mb-6 text-2xl font-bold">Pricing Card (Flights Enabled)</h2>
            <div className="flex justify-center">
              <PricingCard data={packagePricingJson} />
            </div>
          </section>

          <section>
            <h2 className="mb-6 text-2xl font-bold">Pricing Card (Land Only / Flights Disabled)</h2>
            <div className="flex justify-center">
              <PricingCard data={packagePricingFalseJson} />
            </div>
          </section>

          <section>
            <h2 className="mb-6 text-2xl font-bold">Fare Calendar Card</h2>
            <div className="flex justify-center">
              <FareCalendarCard data={fareCalendarJson} />
            </div>
          </section>
        </div>
      </div>
    </LiveKitRoom>
  );
}
