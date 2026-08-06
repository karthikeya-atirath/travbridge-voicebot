'use client';

import React from 'react';
import {
  ArrowRight,
  Bird,
  Clock,
  Compass,
  Heart,
  Library,
  Moon,
  Palmtree,
  ShoppingBag,
  Trees,
  Users,
  Utensils,
  Waves,
  Wind,
  Zap,
} from 'lucide-react';
import { motion } from 'motion/react';

type ActivityCategory =
  | 'adventure'
  | 'nature'
  | 'culture'
  | 'family'
  | 'relaxation'
  | 'nightlife'
  | 'shopping'
  | 'food'
  | 'water'
  | 'wildlife'
  | 'romantic'
  | 'seasonal'
  | 'general';

interface ActivityItem {
  name: string;
  description: string;
  category: ActivityCategory;
  ideal_duration: string;
  image_url: string;
}

interface ActivitiesData {
  destination: string;
  activities_overview: string;
  top_activities: ActivityItem[];
}

interface ActivitiesCardProps {
  data: ActivitiesData;
}

const categoryIcons: Record<ActivityCategory, React.ElementType> = {
  adventure: Zap,
  nature: Trees,
  culture: Library,
  family: Users,
  relaxation: Palmtree,
  nightlife: Moon,
  shopping: ShoppingBag,
  food: Utensils,
  water: Waves,
  wildlife: Bird,
  romantic: Heart,
  seasonal: Wind,
  general: Compass,
};

const categoryColors: Record<ActivityCategory, string> = {
  adventure: 'bg-orange-100 text-orange-600',
  nature: 'bg-emerald-100 text-emerald-600',
  culture: 'bg-indigo-100 text-indigo-600',
  family: 'bg-blue-100 text-blue-600',
  relaxation: 'bg-teal-100 text-teal-600',
  nightlife: 'bg-purple-100 text-purple-600',
  shopping: 'bg-pink-100 text-pink-600',
  food: 'bg-rose-100 text-rose-600',
  water: 'bg-cyan-100 text-cyan-600',
  wildlife: 'bg-amber-100 text-amber-600',
  romantic: 'bg-red-100 text-red-600',
  seasonal: 'bg-slate-100 text-slate-600',
  general: 'bg-zinc-100 text-zinc-600',
};

export function ActivitiesCard({ data }: ActivitiesCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95, y: 15 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
      className="my-4 w-full max-w-full rounded-2xl border border-neutral-200 bg-white/90 p-5 shadow-sm backdrop-blur-md md:max-w-[600px] dark:border-neutral-800 dark:bg-zinc-900/90"
    >
      {/* Dynamic Header */}
      <div className="relative bg-gradient-to-br from-emerald-600 to-teal-800 p-8 text-white">
        <div className="absolute top-0 right-0 p-8 opacity-10">
          <Compass size={120} strokeWidth={1} />
        </div>
        <div className="relative z-10 space-y-4">
          <div className="flex w-fit items-center gap-2 rounded-full bg-white/20 px-4 py-1.5 text-[10px] font-black tracking-widest uppercase backdrop-blur-md">
            <Zap className="h-3 w-3 fill-white" />
            Experience {data.destination}
          </div>
          <h2 className="text-4xl font-black tracking-tighter">Curated Activities</h2>
          <p className="max-w-md text-sm leading-relaxed font-medium text-emerald-50/80">
            {data.activities_overview}
          </p>
        </div>
      </div>

      <div className="p-8">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {data.top_activities.map((activity, i) => {
            const Icon = categoryIcons[activity.category] || Compass;
            return (
              <motion.div
                key={i}
                whileHover={{ y: -4 }}
                className="flex flex-col rounded-2xl border border-zinc-100 bg-white p-4 shadow-md transition-shadow hover:shadow-xl"
              >
                <div className="relative mb-4 h-40 w-full overflow-hidden rounded-xl bg-zinc-100">
                  {activity.image_url ? (
                    <img
                      src={activity.image_url}
                      alt={activity.name}
                      className="h-full w-full object-cover"
                    />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center">
                      <Icon className="h-12 w-12 text-zinc-300" />
                    </div>
                  )}
                  <div
                    className={`absolute top-2 left-2 flex items-center gap-1 rounded-lg px-2 py-1 text-[8px] font-black tracking-widest uppercase shadow-sm ${categoryColors[activity.category]}`}
                  >
                    <Icon size={10} />
                    {activity.category}
                  </div>
                </div>

                <div className="flex-1 space-y-2">
                  <h3 className="text-sm font-black tracking-tight text-zinc-800 uppercase">
                    {activity.name}
                  </h3>
                  <p className="line-clamp-2 text-[11px] leading-relaxed font-medium text-zinc-500">
                    {activity.description}
                  </p>
                </div>

                <div className="mt-4 flex items-center justify-between border-t border-zinc-50 pt-3 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
                  <div className="flex items-center gap-1.5">
                    <Clock size={12} className="text-emerald-500" />
                    {activity.ideal_duration}
                  </div>
                  <ArrowRight size={12} className="text-zinc-300" />
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>

      <div className="flex items-center justify-between bg-emerald-50/50 px-8 py-4 text-[9px] font-black tracking-widest text-emerald-700/50 uppercase italic">
        <span>Adventure Awaits</span>
        <div className="flex items-center gap-2">
          <Trees size={10} />
          Verified Local Experiences
        </div>
      </div>
    </motion.div>
  );
}
