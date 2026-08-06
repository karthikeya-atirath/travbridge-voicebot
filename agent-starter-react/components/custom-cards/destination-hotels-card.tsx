'use client';

import React from 'react';
import { ArrowUpRight, Hotel, MapPin, Star } from 'lucide-react';
import { motion } from 'motion/react';

export interface HotelItem {
  name: string;
  description: string;
  rating?: number;
  address: string;
  image_url: string;
}

export interface DestinationHotelsData {
  destination: string;
  custom_input: string;
  hotel_overview: string;
  hotels: HotelItem[];
}

interface DestinationHotelsCardProps {
  data: DestinationHotelsData;
}

export function DestinationHotelsCard({ data }: DestinationHotelsCardProps) {
  const hotels = data.hotels || [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Header Section */}
      <div className="relative overflow-hidden bg-zinc-900 px-8 py-10 text-white">
        <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/carbon-fibre.png')] opacity-10" />
        <div className="absolute -top-12 -right-12 h-64 w-64 rounded-full bg-emerald-500/20 blur-3xl" />

        <div className="relative z-10 space-y-4">
          <div className="flex w-fit items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-[10px] font-black tracking-widest uppercase backdrop-blur-md">
            <Hotel className="h-3 w-3 text-emerald-400" />
            Premium Stays
          </div>
          <h2 className="text-4xl font-black tracking-tighter">Stay in {data.destination}</h2>
          <p className="max-w-md text-sm leading-relaxed font-medium text-zinc-400">
            {data.hotel_overview ||
              `Exploring the best accommodation options for your visit to ${data.destination}.`}
          </p>
        </div>
      </div>

      <div className="space-y-6 p-8">
        <div className="grid grid-cols-1 gap-6">
          {hotels.map((hotel, idx) => (
            <motion.div
              key={hotel.name + idx}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.1 * idx }}
              className="group relative flex flex-col gap-6 rounded-2xl border border-zinc-100 bg-zinc-50/50 p-4 transition-all hover:border-emerald-100 hover:bg-white hover:shadow-xl md:flex-row"
            >
              {/* Hotel Image */}
              <div className="relative h-48 w-full shrink-0 overflow-hidden rounded-xl md:h-32 md:w-48">
                {hotel.image_url ? (
                  <img
                    src={hotel.image_url}
                    alt={hotel.name}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-emerald-50">
                    <Hotel className="h-8 w-8 text-emerald-200" />
                  </div>
                )}
                {hotel.rating && (
                  <div className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-black text-white backdrop-blur-sm">
                    <Star className="h-2.5 w-2.5 fill-yellow-400 text-yellow-400" />
                    {hotel.rating}
                  </div>
                )}
              </div>

              {/* Hotel Details */}
              <div className="flex flex-1 flex-col justify-between py-1">
                <div className="space-y-2">
                  <div className="flex items-start justify-between">
                    <h3 className="text-lg font-black tracking-tight text-zinc-800">
                      {hotel.name}
                    </h3>
                    <ArrowUpRight className="h-4 w-4 text-zinc-300 transition-colors group-hover:text-emerald-500" />
                  </div>
                  <p className="line-clamp-2 text-xs leading-relaxed font-medium text-zinc-500">
                    {hotel.description}
                  </p>
                </div>

                <div className="mt-4 flex items-center gap-1.5 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
                  <MapPin className="h-3.5 w-3.5 text-emerald-500" />
                  <span className="line-clamp-1">{hotel.address}</span>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-zinc-100 bg-zinc-50 px-8 py-4">
        <span className="text-[9px] font-black tracking-widest text-zinc-400 uppercase italic">
          Verified Accommodations
        </span>
        <div className="flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1 text-[9px] font-bold text-zinc-500">
          <Star size={10} className="fill-emerald-500 text-emerald-500" />
          Hand-picked selection
        </div>
      </div>
    </motion.div>
  );
}
