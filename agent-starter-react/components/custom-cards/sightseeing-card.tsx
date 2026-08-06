'use client';

import React from 'react';
import { ArrowUpRight, Calendar, Clock, MapPin, Star } from 'lucide-react';
import { motion } from 'motion/react';

interface SightseeingItem {
  name: string;
  description: string;
  ideal_duration: string;
  best_time_to_visit: string;
  image_url: string;
}

interface SightseeingData {
  destination: string;
  sightseeing_overview: string;
  top_sightseeing: SightseeingItem[];
}

interface SightseeingCardProps {
  data: SightseeingData;
}

export function SightseeingCard({ data }: SightseeingCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Header with Background Pattern */}
      <div className="relative overflow-hidden bg-zinc-900 px-8 py-10 text-white">
        <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/carbon-fibre.png')] opacity-10" />
        <div className="absolute -top-12 -right-12 h-64 w-64 rounded-full bg-blue-500/20 blur-3xl" />

        <div className="relative z-10 space-y-4">
          <div className="flex w-fit items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-[10px] font-black tracking-widest uppercase backdrop-blur-md">
            <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
            Top Recommendations
          </div>
          <h2 className="text-4xl font-black tracking-tighter">
            Sightseeing in {data.destination}
          </h2>
          <p className="max-w-md text-sm leading-relaxed font-medium text-zinc-400">
            {data.sightseeing_overview}
          </p>
        </div>
      </div>

      {/* Sightseeing Grid */}
      <div className="space-y-8 p-8">
        <div className="grid grid-cols-1 gap-6">
          {data.top_sightseeing.map((item, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -20 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.1 }}
              className="group relative flex flex-col gap-6 rounded-2xl border border-zinc-100 bg-zinc-50/50 p-4 transition-all hover:border-blue-100 hover:bg-white hover:shadow-xl md:flex-row"
            >
              {/* Image Container */}
              <div className="relative h-48 w-full shrink-0 overflow-hidden rounded-xl md:h-32 md:w-48">
                {item.image_url ? (
                  <img
                    src={item.image_url}
                    alt={item.name}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-zinc-200">
                    <MapPin className="h-8 w-8 text-zinc-400" />
                  </div>
                )}
                <div className="absolute inset-0 flex items-end bg-gradient-to-t from-black/60 to-transparent p-3 opacity-0 transition-opacity group-hover:opacity-100">
                  <span className="flex items-center gap-1 text-[10px] font-bold tracking-wider text-white uppercase">
                    View Details <ArrowUpRight size={10} />
                  </span>
                </div>
              </div>

              {/* Content */}
              <div className="flex flex-col justify-between py-1">
                <div className="space-y-2">
                  <h3 className="text-lg font-black tracking-tight text-zinc-800">{item.name}</h3>
                  <p className="line-clamp-2 text-xs leading-relaxed font-medium text-zinc-500">
                    {item.description}
                  </p>
                </div>

                <div className="mt-4 flex flex-wrap gap-4">
                  <div className="flex items-center gap-1.5 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
                    <Clock className="h-3 w-3 text-blue-500" />
                    {item.ideal_duration}
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
                    <Calendar className="h-3 w-3 text-emerald-500" />
                    {item.best_time_to_visit}
                  </div>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-zinc-100 bg-zinc-50 px-8 py-4">
        <span className="text-[9px] font-black tracking-widest text-zinc-400 uppercase italic">
          Curated Sightseeing Guide
        </span>
        <div className="flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1 text-[9px] font-bold text-zinc-500">
          <MapPin size={10} className="text-blue-500" />
          {data.destination}
        </div>
      </div>
    </motion.div>
  );
}
