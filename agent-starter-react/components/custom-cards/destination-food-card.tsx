'use client';

import React from 'react';
import { Drumstick, Info, Leaf, MapPin, Utensils, Waves } from 'lucide-react';
import { motion } from 'motion/react';

export interface FoodItem {
  name: string;
  description: string;
  where_to_try: string;
  dietary_note: string;
  image_url: string;
}

export interface DestinationFoodData {
  destination: string;
  food_overview: string;
  must_try_foods: FoodItem[];
}

interface DestinationFoodCardProps {
  data: DestinationFoodData;
}

export function DestinationFoodCard({ data }: DestinationFoodCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white shadow-xl md:max-w-[600px]"
    >
      {/* Header Section */}
      <div className="bg-gradient-to-r from-orange-400 to-red-500 p-8 text-white">
        <div className="mb-2 flex items-center gap-2 text-xs font-bold tracking-widest text-white/80 uppercase">
          <Utensils className="h-4 w-4" />
          Culinary Journey
        </div>
        <h2 className="mb-4 text-3xl font-black tracking-tight">Taste of {data.destination}</h2>
        <p className="max-w-lg text-sm leading-relaxed font-medium text-white/90">
          {data.food_overview}
        </p>
      </div>

      <div className="space-y-6 p-8">
        <h3 className="flex items-center gap-2 text-xs font-bold tracking-wider text-zinc-400 uppercase">
          Must-Try Local Dishes
        </h3>

        <div className="space-y-4">
          {data.must_try_foods.map((food, idx) => (
            <motion.div
              key={food.name}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: idx * 0.1 }}
              className="group flex flex-col gap-6 rounded-xl border border-transparent p-4 transition-all hover:border-zinc-200 hover:bg-zinc-50 md:flex-row"
            >
              <div className="h-32 w-full shrink-0 overflow-hidden rounded-lg border border-zinc-100 shadow-sm md:w-48">
                {food.image_url ? (
                  <img
                    src={food.image_url}
                    alt={food.name}
                    className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center bg-zinc-100">
                    <Utensils className="h-8 w-8 text-zinc-300" />
                  </div>
                )}
              </div>

              <div className="flex flex-1 flex-col justify-between space-y-3">
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <h4 className="text-lg leading-tight font-bold tracking-tight text-zinc-900 uppercase">
                      {food.name}
                    </h4>
                  </div>
                  <p className="text-sm leading-relaxed font-medium text-zinc-600">
                    {food.description}
                  </p>
                </div>

                <div className="flex flex-wrap gap-4 pt-1">
                  <div className="flex items-center gap-1.5 text-xs font-bold text-zinc-500 uppercase">
                    <MapPin className="h-3 w-3 text-orange-500" />
                    {food.where_to_try}
                  </div>
                  {food.dietary_note && (
                    <div className="flex items-center gap-1.5 text-xs font-bold text-zinc-500 uppercase">
                      <Info className="h-3 w-3 text-blue-500" />
                      {food.dietary_note}
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>

      {/* Footer Branding */}
      <div className="flex items-center justify-between border-t border-zinc-100 bg-zinc-50 px-8 py-4 text-[10px] font-black tracking-tighter text-zinc-400 uppercase">
        <span>Authentic flavors from {data.destination}</span>
        <div className="flex gap-4">
          <span className="flex items-center gap-1">
            <Leaf className="h-3 w-3" /> Vegan Options available
          </span>
          <span className="flex items-center gap-1">
            <Waves className="h-3 w-3" /> Seafood Specialties
          </span>
        </div>
      </div>
    </motion.div>
  );
}
