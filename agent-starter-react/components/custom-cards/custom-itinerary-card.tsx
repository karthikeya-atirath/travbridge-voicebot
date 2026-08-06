'use client';

import React, { useEffect, useRef } from 'react';
import { motion } from 'motion/react';
import {
  Calendar,
  ChevronRight,
  MapPin,
  Sparkles,
  Users,
} from 'lucide-react';
import { useItineraryPanel } from '@/hooks/use-itinerary-panel';

/* ────────── types ────────── */

interface ItineraryData {
  title?: string;
  destination?: string;
  trip_type?: string;
  from_city?: string;
  start_date?: string;
  end_date?: string;
  adults?: number;
  children?: number;
  infants?: number;
  days?: any[];
  total_estimated_budget?: string;
  hero_image_url?: string;
  [key: string]: any;
}

interface CustomItineraryCardProps {
  data: ItineraryData;
  cardId?: string;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Compact Itinerary Preview Card (shown inline in chat)
   Clicking "Open" pushes the itinerary into the side panel.
   ═══════════════════════════════════════════════════════════════════════════ */

export function CustomItineraryCard({ data, cardId }: CustomItineraryCardProps) {
  const { pushItinerary, openItinerary, allItineraries } = useItineraryPanel();
  const hasPushedRef = useRef(false);

  const days = data.days || [];
  const dayCount = days.length;
  const totalTravellers = (data.adults || 0) + (data.children || 0) + (data.infants || 0);

  // Auto-push to panel ONLY ONCE on mount + ensure data is ready
  useEffect(() => {
    if (!hasPushedRef.current && data && (data.title || data.destination)) {
      const id = cardId || `itinerary-${Date.now()}`;
      pushItinerary(id, data);
      hasPushedRef.current = true;
    }
  }, [data, cardId, pushItinerary]);

  const handleOpen = () => {
    const id = cardId || `itinerary-${Date.now()}`;
    const existing = allItineraries.find((it) => it.id === id);
    if (existing) {
      openItinerary(existing);
    } else {
      pushItinerary(id, data);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="group w-full max-w-full cursor-pointer overflow-hidden rounded-3xl border border-white/20 bg-white/10 shadow-2xl backdrop-blur-xl transition-all hover:scale-[1.02] hover:bg-white/20 md:max-w-[440px] dark:border-white/10 dark:bg-black/20 dark:shadow-black/50"
      onClick={handleOpen}
    >
      <div className="relative h-44 w-full overflow-hidden">
        {data.hero_image_url ? (
          <img
            src={data.hero_image_url}
            alt=""
            className="h-full w-full object-cover transition-transform duration-1000 group-hover:scale-110"
          />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/80 via-purple-600/80 to-blue-700/80" />
        )}
        
        {/* Mirror Gradient Overlay */}
        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
        <div className="absolute inset-0 bg-gradient-to-tr from-indigo-500/10 to-transparent" />

        {/* Badges */}
        <div className="absolute top-4 left-4 flex items-center gap-2">
          <div className="flex items-center gap-1.5 rounded-full border border-white/30 bg-white/20 px-3 py-1.5 text-[10px] font-black tracking-[0.1em] text-white uppercase backdrop-blur-md">
            <Sparkles className="h-3 w-3 animate-pulse text-amber-300" />
            <span>AI Itinerary</span>
          </div>
          {data.trip_type && (
            <div className="rounded-full bg-indigo-500/50 px-3 py-1.5 text-[10px] font-bold tracking-wider text-white uppercase backdrop-blur-md">
              {data.trip_type}
            </div>
          )}
        </div>

        {/* Title Section */}
        <div className="absolute bottom-4 left-6 right-6">
          <h3 className="text-xl font-black leading-tight tracking-tight text-white drop-shadow-lg lg:text-2xl">
            {data.title || `Custom Trip to ${data.destination || 'Destination'}`}
          </h3>
        </div>
      </div>

      {/* Details Bar */}
      <div className="px-6 py-5">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[11px] font-bold text-zinc-600 dark:text-zinc-400">
          <span className="flex items-center gap-1.5 rounded-lg bg-zinc-400/10 px-2 py-1">
            <Calendar className="h-3.5 w-3.5 text-indigo-500" />
            {data.start_date || 'TBD'} — {data.end_date || 'TBD'}
          </span>
          <span className="flex items-center gap-1.5">
            <MapPin className="h-3.5 w-3.5 text-indigo-500" />
            {data.destination || 'Travels'}
          </span>
          <span className="flex items-center gap-1.5">
            <Users className="h-3.5 w-3.5 text-indigo-500" />
            {totalTravellers || 1}
          </span>
        </div>

        <div className="mt-5 flex items-center justify-between">
          <div className="space-y-0.5">
            <div className="text-[10px] font-black tracking-widest text-zinc-400 uppercase">Estimated Budget</div>
            <div className="text-lg font-black text-indigo-700 dark:text-indigo-400">
              {data.total_estimated_budget || 'Calculating...'}
            </div>
          </div>

          <button
            onClick={(e) => {
              e.stopPropagation();
              handleOpen();
            }}
            className="group relative flex items-center gap-2 overflow-hidden rounded-2xl bg-indigo-600 px-6 py-3 text-sm font-black tracking-tighter text-white shadow-xl shadow-indigo-500/30 transition-all hover:bg-indigo-700 active:scale-95"
          >
            <span className="relative z-10 uppercase">Explore Plan</span>
            <ChevronRight className="relative z-10 h-4 w-4 transition-transform group-hover:translate-x-1" />
            <div className="absolute inset-0 z-0 bg-gradient-to-r from-white/0 via-white/10 to-white/0 opacity-0 transition-opacity group-hover:animate-shimmer group-hover:opacity-100" />
          </button>
        </div>
      </div>
      
      {/* Footer Sparkle */}
      <div className="flex items-center justify-between border-t border-white/10 bg-white/5 px-6 py-3 dark:bg-black/20">
         <div className="flex items-center gap-1.5">
           {[...Array(dayCount > 5 ? 5 : dayCount)].map((_, i) => (
             <div key={i} className="h-1 w-1 rounded-full bg-indigo-500" />
           ))}
           <span className="text-[10px] font-bold text-zinc-500">{dayCount} Days of adventure</span>
         </div>
         <Sparkles size={12} className="text-zinc-300 dark:text-zinc-600" />
      </div>
    </motion.div>
  );
}
