'use client';

import React from 'react';
import { Calendar, CheckCircle2, Compass, Info, MapPin } from 'lucide-react';
import { motion } from 'motion/react';

export interface PlaceToVisit {
  name: string;
  description: string;
  best_for: string[];
  image_url: string;
}

export interface DestinationInfoData {
  destination: string;
  short_overview: string;
  best_time_to_visit: string;
  top_places_to_visit: PlaceToVisit[];
  hero_image_url: string;
  practical_highlights: string[];
}

interface DestinationInfoCardProps {
  data: DestinationInfoData;
}

export function DestinationInfoCard({ data }: DestinationInfoCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Hero Section */}
      <div className="relative h-64 w-full overflow-hidden bg-zinc-100">
        {data.hero_image_url ? (
          <img
            src={data.hero_image_url}
            alt={data.destination}
            className="h-full w-full object-cover transition-transform duration-700 hover:scale-105"
          />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-br from-blue-500 to-indigo-700" />
        )}
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/20 to-transparent" />
        <div className="absolute right-6 bottom-6 left-6">
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.2 }}
            className="mb-1 flex items-center gap-2 text-xs font-bold tracking-widest text-white/80 uppercase"
          >
            <MapPin className="h-3 w-3" />
            Destination Guide
          </motion.div>
          <motion.h2
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 }}
            className="text-4xl font-black tracking-tight text-white"
          >
            {data.destination}
          </motion.h2>
        </div>
      </div>

      <div className="space-y-8 p-8">
        {/* Overview & Best Time */}
        <div className="grid grid-cols-1 gap-8 font-medium md:grid-cols-2">
          <div className="space-y-3">
            <h3 className="flex items-center gap-2 text-xs font-bold tracking-wider text-blue-600 uppercase">
              <Compass className="h-4 w-4" />
              Overview
            </h3>
            <p className="text-sm leading-relaxed text-zinc-600">{data.short_overview}</p>
          </div>
          <div className="space-y-3">
            <h3 className="flex items-center gap-2 text-xs font-bold tracking-wider text-orange-600 uppercase">
              <Calendar className="h-4 w-4" />
              Best Time to Visit
            </h3>
            <p className="text-sm leading-relaxed text-zinc-600">{data.best_time_to_visit}</p>
          </div>
        </div>

        {/* Top Places */}
        <div className="space-y-4">
          <h3 className="flex items-center gap-2 text-xs font-bold tracking-wider text-purple-600 uppercase">
            <Compass className="h-4 w-4" />
            Top Places to Visit
          </h3>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {data.top_places_to_visit.map((place, idx) => (
              <motion.div
                key={place.name}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.4 + idx * 0.1 }}
                className="group relative flex flex-col overflow-hidden rounded-xl border border-zinc-100 bg-zinc-50 transition-all hover:border-zinc-200 hover:shadow-md"
              >
                <div className="h-32 w-full overflow-hidden">
                  <img
                    src={place.image_url}
                    alt={place.name}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                  />
                </div>
                <div className="space-y-2 p-4">
                  <h4 className="text-sm font-bold text-zinc-800">{place.name}</h4>
                  <p className="line-clamp-2 text-xs text-zinc-500">{place.description}</p>
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {place.best_for.slice(0, 3).map((tag) => (
                      <span
                        key={tag}
                        className="rounded border border-zinc-200 bg-white px-2 py-0.5 text-[10px] whitespace-nowrap text-neutral-500"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>

        {/* Practical Highlights */}
        <div className="space-y-4 rounded-xl border border-blue-100/50 bg-blue-50/50 p-6">
          <h3 className="flex items-center gap-2 text-xs font-bold tracking-wider text-blue-700 uppercase">
            <Info className="h-4 w-4" />
            Travel Essentials
          </h3>
          <div className="grid grid-cols-1 gap-3">
            {data.practical_highlights.slice(0, 4).map((highlight, idx) => (
              <div key={idx} className="flex gap-3 text-sm">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
                <p className="leading-snug text-zinc-600">{highlight}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
