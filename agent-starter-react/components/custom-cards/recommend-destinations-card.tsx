'use client';

import React from 'react';
import { ArrowRight, Compass, MapPin, Sparkles } from 'lucide-react';
import { motion } from 'motion/react';

export interface RecommendedDestination {
  name: string;
  description: string;
  best_for: string[];
  image_url: string;
}

export interface RecommendDestinationsData {
  custom_input: string;
  recommended_destinations: RecommendedDestination[];
}

interface RecommendDestinationsCardProps {
  data: RecommendDestinationsData;
}

export function RecommendDestinationsCard({ data }: RecommendDestinationsCardProps) {
  const recommendations = data.recommended_destinations || [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Header Section */}
      <div className="relative overflow-hidden bg-zinc-900 px-8 py-10 text-white">
        <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/carbon-fibre.png')] opacity-10" />
        <div className="absolute -top-12 -right-12 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />

        <div className="relative z-10 space-y-4">
          <div className="flex w-fit items-center gap-2 rounded-full bg-white/10 px-3 py-1 text-[10px] font-black tracking-widest uppercase backdrop-blur-md">
            <Sparkles className="h-3 w-3 text-amber-400" />
            Personalized Discoveries
          </div>
          <h2 className="text-4xl font-black tracking-tighter">Destination Discovery</h2>
          <p className="max-w-md text-sm leading-relaxed font-medium text-zinc-400">
            Based on your interest in &quot;{data.custom_input}&quot;, here are some hand-picked
            escapes for you.
          </p>
        </div>
      </div>

      <div className="space-y-6 p-8">
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2">
          {recommendations.map((dest, idx) => (
            <motion.div
              key={dest.name}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.1 * idx }}
              className="group relative flex flex-col overflow-hidden rounded-2xl border border-zinc-100 bg-zinc-50/50 transition-all hover:border-indigo-100 hover:bg-white hover:shadow-xl"
            >
              <div className="relative h-40 w-full overflow-hidden">
                {dest.image_url ? (
                  <img
                    src={dest.image_url}
                    alt={dest.name}
                    className="h-full w-full object-cover transition-transform duration-700 group-hover:scale-110"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-indigo-50">
                    <Compass className="h-10 w-10 text-indigo-200" />
                  </div>
                )}
                <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
                <div className="absolute bottom-3 left-3 translate-y-2 opacity-0 transition-all group-hover:translate-y-0 group-hover:opacity-100">
                  <span className="flex items-center gap-1 text-[10px] font-bold tracking-wider text-white uppercase">
                    Explore <ArrowRight size={10} />
                  </span>
                </div>
              </div>

              <div className="flex flex-1 flex-col p-4">
                <h3 className="mb-1 text-lg font-black tracking-tight text-zinc-800">
                  {dest.name}
                </h3>
                <p className="mb-4 line-clamp-2 text-xs leading-relaxed font-medium text-zinc-500">
                  {dest.description}
                </p>
                <div className="mt-auto flex flex-wrap gap-1.5">
                  {dest.best_for.map((tag) => (
                    <span
                      key={tag}
                      className="rounded-md border border-zinc-200 bg-white px-2 py-0.5 text-[9px] font-bold tracking-tighter text-zinc-400 uppercase"
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

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-zinc-100 bg-zinc-50 px-8 py-4">
        <span className="text-[9px] font-black tracking-widest text-zinc-400 uppercase italic">
          Powered by Global Insights
        </span>
        <div className="flex items-center gap-1.5 rounded-full border border-zinc-200 bg-white px-3 py-1 text-[9px] font-bold text-zinc-500">
          <MapPin size={10} className="text-indigo-500" />
          Tailored for you
        </div>
      </div>
    </motion.div>
  );
}
