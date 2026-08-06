'use client';

import React from 'react';
import { Room } from 'livekit-client';
import { LiveKitRoom } from '@livekit/components-react';
import {
  ActivitiesCard,
  DestinationFlightsCard,
  DestinationFoodCard,
  DestinationHotelsCard,
  DestinationInfoCard,
  FareCalendarCard,
  PackageList,
  PricingCard,
  RecommendDestinationsCard,
  SightseeingCard,
  VisaInfoCard,
  WeatherCard,
} from '@/components/custom-cards';

const dummyRoom = new Room();

const MOCK_DATA = {
  packageList: [
    {
      packageId: 'PKG1',
      packageName: 'Jordan And Egyptian Extravaganza - Summer 2026 Special Edition with Nile Cruise and Petra Tour',
      duration: '12N / 13D',
      price: 356669,
      imageUrl: '/images/dubai.jpg',
    },
    {
      packageId: 'PKG2',
      packageName: 'Simply Dubai',
      price: 66111,
      duration: '5N/6D',
      imageUrl: 'https://images.unsplash.com/photo-1518684079-3c830dcef090?auto=format&fit=crop&q=80&w=800',
    },
    {
      packageId: 'PKG3',
      packageName: 'Trending Dubai',
      price: 67445,
      duration: '6N/7D',
      imageUrl: 'https://images.unsplash.com/photo-1546412414-e1885259563a?auto=format&fit=crop&q=80&w=800',
    },
    {
      packageId: 'PKG4',
      packageName: 'Luxury Dubai Escape',
      price: 85000,
      duration: '5N/6D',
      imageUrl: 'https://images.unsplash.com/photo-1582650625119-3a31f8fa2699?auto=format&fit=crop&q=80&w=800',
    }
  ],
  flights: {
    flight_results: {
      trip: 'int',
      from_city: 'Hyderabad',
      to_city: 'Dubai',
      depart: '24-04-2026',
      total_found: 119,
      flights: [
        {
          airline_name: 'IndiGo',
          airline_code: '6E',
          flight_number: '6E-1',
          departure_time: '23:55',
          arrival_time: '02:00',
          from_city: 'HYD',
          to_city: 'RKT',
          stops_text: 'NON-STOP',
          duration_text: '3hr 35m',
          price: '32426',
          recommended: true,
          airline_logo_url: 'https://images.travbridge.com/airline_logos/6E.png',
          segments: []
        },
        {
          airline_name: 'Emirates Airline',
          airline_code: 'EK',
          flight_number: 'EK-1',
          departure_time: '04:40',
          arrival_time: '06:45',
          from_city: 'HYD',
          to_city: 'DXB',
          stops_text: 'NON-STOP',
          duration_text: '3hr 35m',
          price: '52310',
          recommended: true,
          airline_logo_url: 'https://images.travbridge.com/airline_logos/EK.png',
          segments: []
        }
      ]
    },
    meta: { noofflights: 119, nonstopflights: 5, noOfRecommendedFlight: 2, memcacheKey: '', validFrom: '' },
    code: 200
  },
  food: {
    destination: 'Dubai',
    food_overview: 'Dubai offers a melting pot of culinary experiences, from traditional Emirati dishes to world-class international cuisine.',
    must_try_foods: [
      { name: 'Machboos', description: 'Spice-laden rice mixed with meat (usually chicken or lamb).', where_to_try: 'Al Fanar Restaurant', dietary_note: 'Contains Rice & Meat', image_url: '' },
      { name: 'Shawarma', description: 'Slow-cooked meat wrapped in bread with garlic sauce and vegetables.', where_to_try: 'Local streets of Deira', dietary_note: 'Contains Gluten & Meat', image_url: '' }
    ]
  },
  hotels: {
    destination: 'Dubai',
    hotel_overview: 'From the iconic Burj Al Arab to boutique stays in Al Fahidi, Dubai has accommodation for every traveler.',
    hotels: [
      { name: 'Atlantis The Royal', description: 'A new landmark resort with world-class dining and amenities.', address: 'Palm Jumeirah, Dubai', rating: 5, image_url: '' },
      { name: 'Rove Downtown', description: 'A trendy, affordable hotel steps away from Dubai Mall.', address: 'Zabeel 2, Dubai', rating: 3, image_url: '' }
    ]
  },
  destinationInfo: {
    destination: 'Dubai',
    short_overview: 'Dubai is a city and emirate in the United Arab Emirates luxury shopping, ultramodern architecture and a lively nightlife scene.',
    best_time_to_visit: 'November to March',
    top_places_to_visit: [
      { name: 'Burj Khalifa', description: 'The world\'s tallest building.', best_for: ['Families', 'Photography'], image_url: '' },
      { name: 'Dubai Mall', description: 'One of the world\'s largest shopping malls.', best_for: ['Shopping', 'Families'], image_url: '' }
    ],
    hero_image_url: '',
    practical_highlights: ['Currency: AED', 'Language: Arabic & English', 'Timezone: GMT+4']
  },
  fareCalendar: {
    departureCities: [{
      dates: {
        bookable: {
          '2026-04': { '01': { p: 55000 }, '05': { p: 52000 }, '10': { p: 58000 } },
          '2026-05': { '02': { p: 51000 }, '15': { p: 53000 } }
        }
      }
    }]
  },
  pricing: {
    totalPrice: 125000,
    totalTax: 15000,
    totalDiscount: 5000,
    currencySummary: 'AED',
    rooms: [{ roomNo: 1, noAdult: 2, noCwb: 0, noCnbJ: 0, noCnbS: 0, inf: 0 }],
    flights: []
  },
  recommendations: {
    custom_input: 'Modern Cities with Beaches',
    recommended_destinations: [
      { name: 'Singapore', description: 'A global financial hub with amazing skyline and gardens.', best_for: ['Families', 'Food'], image_url: '' },
      { name: 'Miami', description: 'Famous for its beaches, nightlife, and Art Deco district.', best_for: ['Parties', 'Beach'], image_url: '' }
    ]
  },
  sightseeing: {
    destination: 'Dubai',
    sightseeing_overview: 'Explore the highlights of Dubai, from the historic creek to the futuristic palm islands.',
    top_sightseeing: [
      { name: 'Dubai Fountain', description: 'A choreographed fountain system set on the Burj Khalifa Lake.', ideal_duration: '30 mins', best_time_to_visit: 'Evening', image_url: '' },
      { name: 'Global Village', description: 'A multi-cultural theme park and shopping destination.', ideal_duration: '4-5 hours', best_time_to_visit: 'Winter', image_url: '' }
    ]
  },
  visaInfo: {
    destination: 'Dubai',
    visa_required: 'yes',
    visa_type: 'Electronic Tourist Visa',
    processing_time: '3-5 Working Days',
    validity_info: '30 Days from entry',
    stay_duration_info: '30 Days',
    documents_required: ['Passport Copy', 'Photograph', 'Flight Tickets'],
    visa_notes: ['Ensure passport has 6 months validity.', 'Visa is for single entry only.']
  },
  weather: {
    destination: 'Dubai',
    month: 'April',
    weather_type: 'sunny',
    weather_summary: 'Expect warm and sunny days with clear skies.',
    average_temperature_range: '24°C - 33°C',
    humidity_or_rain_context: 'Low humidity, virtually no rain.',
    what_to_pack: ['Sunscreen', 'Cotton clothes', 'Sunglasses'],
    travel_advice: ['Stay hydrated', 'Wear light colors'],
    suitable_activities: ['Beach', 'Theme Parks']
  },
  activities: {
    destination: 'Dubai',
    activities_overview: 'Dubai offers a wide range of activities from desert safaris to indoor skiing.',
    top_activities: [
      { name: 'Desert Safari', description: 'Dune bashing, camel riding, and traditional dinner.', category: 'adventure', ideal_duration: '6 hours', image_url: '' },
      { name: 'Skydiving', description: 'Experience the thrill of skydiving over Palm Jumeirah.', category: 'adventure', ideal_duration: '3 hours', image_url: '' }
    ]
  }
};

export default function TestCardsPage() {
  return (
    <LiveKitRoom room={dummyRoom} serverUrl="wss://dummy.livekit.cloud" token="dummy-token">
      <div className="min-h-screen bg-zinc-100/50 p-4 md:p-8 dark:bg-zinc-950">
        <div className="mx-auto max-w-none space-y-16">
          <header className="text-center">
            <h1 className="text-4xl font-black tracking-tight text-zinc-900 dark:text-white">Card Component Gallery</h1>
            <p className="mt-2 text-zinc-500">Verification page for all custom cards (Mobile & Desktop)</p>
          </header>

          {/* Wrapper for cards that simulates the chat transcript bubble */}
          <section className="w-full space-y-8 overflow-hidden">
            <h2 className="text-xl font-bold border-b pb-2">Inside Chat Transcript</h2>
            <div className="flex w-full flex-col gap-6 rounded-3xl border border-neutral-200 bg-white/50 p-4 md:p-6 shadow-sm backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-900/50">
              <div className="w-full space-y-12">
                <div className="w-full overflow-hidden">
                  <h3 className="mb-4 text-xs font-black uppercase tracking-widest text-zinc-400">Package List</h3>
                  <div className="flex justify-start">
                    <PackageList data={MOCK_DATA.packageList} />
                  </div>
                </div>

                <div>
                  <h3 className="mb-4 text-xs font-black uppercase tracking-widest text-zinc-400">Flight Results</h3>
                  <div className="flex justify-start">
                    <DestinationFlightsCard data={MOCK_DATA.flights as any} />
                  </div>
                </div>

                <div>
                  <h3 className="mb-4 text-xs font-black uppercase tracking-widest text-zinc-400">Weather</h3>
                  <div className="flex justify-start">
                    <WeatherCard data={MOCK_DATA.weather as any} />
                  </div>
                </div>

                <div>
                  <h3 className="mb-4 text-xs font-black uppercase tracking-widest text-zinc-400">Visa Info</h3>
                  <div className="flex justify-start">
                    <VisaInfoCard data={MOCK_DATA.visaInfo as any} />
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="space-y-8">
            <h2 className="text-xl font-bold border-b pb-2">Individual Components</h2>
            
            <div className="space-y-16">
               <div>
                <h3 className="mb-6 text-2xl font-black">Food Recommendations</h3>
                <DestinationFoodCard data={MOCK_DATA.food as any} />
              </div>

              <div>
                <h3 className="mb-6 text-2xl font-black">Hotels</h3>
                <DestinationHotelsCard data={MOCK_DATA.hotels as any} />
              </div>

               <div>
                <h3 className="mb-6 text-2xl font-black">Activities</h3>
                <ActivitiesCard data={MOCK_DATA.activities as any} />
              </div>

               <div>
                <h3 className="mb-6 text-2xl font-black">Pricing Details</h3>
                <PricingCard data={MOCK_DATA.pricing as any} />
              </div>

               <div>
                <h3 className="mb-6 text-2xl font-black">Fare Calendar</h3>
                <FareCalendarCard data={MOCK_DATA.fareCalendar as any} />
              </div>
            </div>
          </section>
        </div>
      </div>
    </LiveKitRoom>
  );
}
