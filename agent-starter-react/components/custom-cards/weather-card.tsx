'use client';

import React from 'react';
import {
  Calendar,
  CheckCircle2,
  Cloud,
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSun,
  IceCream,
  Info,
  Layers,
  MapPin,
  Snowflake,
  Sun,
  Thermometer,
  ThermometerSun,
  Waves,
  Wind,
} from 'lucide-react';
import { motion } from 'motion/react';

export type WeatherType =
  | 'sunny'
  | 'partly_cloudy'
  | 'cloudy'
  | 'rainy'
  | 'stormy'
  | 'snowy'
  | 'windy'
  | 'humid'
  | 'cold'
  | 'hot'
  | 'mixed';

export interface WeatherData {
  destination: string;
  month: string;
  weather_type: WeatherType;
  weather_summary: string;
  average_temperature_range: string;
  humidity_or_rain_context: string;
  what_to_pack: string[];
  travel_advice: string[];
  suitable_activities: string[];
}

interface WeatherCardProps {
  data: WeatherData;
}

const weatherThemes: Record<
  WeatherType,
  {
    gradient: string;
    icon: React.ElementType;
    accent: string;
    bgSubtle: string;
    textIcon: string;
  }
> = {
  sunny: {
    gradient: 'from-amber-400 to-orange-500',
    icon: Sun,
    accent: 'text-orange-600',
    bgSubtle: 'bg-orange-50',
    textIcon: 'text-amber-100',
  },
  partly_cloudy: {
    gradient: 'from-blue-400 to-amber-200',
    icon: CloudSun,
    accent: 'text-blue-600',
    bgSubtle: 'bg-blue-50',
    textIcon: 'text-blue-100',
  },
  cloudy: {
    gradient: 'from-zinc-400 to-zinc-500',
    icon: Cloud,
    accent: 'text-zinc-600',
    bgSubtle: 'bg-zinc-100',
    textIcon: 'text-zinc-200',
  },
  rainy: {
    gradient: 'from-indigo-500 to-slate-700',
    icon: CloudRain,
    accent: 'text-indigo-600',
    bgSubtle: 'bg-indigo-50',
    textIcon: 'text-indigo-100',
  },
  stormy: {
    gradient: 'from-purple-700 to-zinc-900',
    icon: CloudLightning,
    accent: 'text-purple-600',
    bgSubtle: 'bg-purple-50',
    textIcon: 'text-purple-200',
  },
  snowy: {
    gradient: 'from-blue-100 to-blue-300',
    icon: Snowflake,
    accent: 'text-blue-500',
    bgSubtle: 'bg-cyan-50',
    textIcon: 'text-blue-400',
  },
  windy: {
    gradient: 'from-teal-400 to-slate-500',
    icon: Wind,
    accent: 'text-teal-600',
    bgSubtle: 'bg-teal-50',
    textIcon: 'text-teal-100',
  },
  humid: {
    gradient: 'from-emerald-400 to-cyan-500',
    icon: ThermometerSun,
    accent: 'text-emerald-600',
    bgSubtle: 'bg-emerald-50',
    textIcon: 'text-emerald-100',
  },
  cold: {
    gradient: 'from-blue-600 to-indigo-900',
    icon: IceCream,
    accent: 'text-blue-700',
    bgSubtle: 'bg-blue-50',
    textIcon: 'text-blue-200',
  },
  hot: {
    gradient: 'from-red-500 to-orange-700',
    icon: Thermometer,
    accent: 'text-red-600',
    bgSubtle: 'bg-red-50',
    textIcon: 'text-red-100',
  },
  mixed: {
    gradient: 'from-rose-400 to-indigo-500',
    icon: CloudFog,
    accent: 'text-rose-600',
    bgSubtle: 'bg-rose-50',
    textIcon: 'text-rose-100',
  },
};

export function WeatherCard({ data }: WeatherCardProps) {
  const theme = weatherThemes[data.weather_type] || weatherThemes.mixed;
  const WeatherIcon = theme.icon;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Dynamic Header */}
      <div
        className={`relative overflow-hidden bg-gradient-to-br ${theme.gradient} p-8 text-white`}
      >
        {/* Abstract Background Elements */}
        <div className="absolute -top-8 -right-8 opacity-20 transition-transform duration-1000 group-hover:rotate-12">
          <WeatherIcon size={240} strokeWidth={1} />
        </div>

        <div className="relative z-10 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 rounded-full bg-white/20 px-4 py-1.5 text-[10px] font-black tracking-widest uppercase backdrop-blur-md">
              <Calendar className="h-3 w-3" />
              {data.month} Forecast
            </div>
            <div className="text-[10px] font-black tracking-widest text-white/60 uppercase">
              {data.weather_type.replace('_', ' ')}
            </div>
          </div>

          <div className="flex items-baseline gap-4">
            <h2 className="text-5xl font-black tracking-tighter">{data.destination}</h2>
            <div className="text-2xl font-medium tracking-tight text-white/80">
              {data.average_temperature_range}
            </div>
          </div>

          <p className="max-w-md text-sm leading-relaxed font-medium text-white/90">
            {data.weather_summary}
          </p>
        </div>
      </div>

      <div className="space-y-8 p-8">
        {/* Core Stats */}
        <div className="grid grid-cols-1 gap-8 font-medium md:grid-cols-2">
          <div className="space-y-3">
            <h3
              className={`flex items-center gap-2 ${theme.accent} text-[10px] font-bold tracking-wider uppercase`}
            >
              <Layers className="h-4 w-4" />
              Conditions
            </h3>
            <p className="text-sm leading-relaxed text-zinc-600 italic">
              &quot;{data.humidity_or_rain_context}&quot;
            </p>
          </div>
          <div className="space-y-3">
            <h3
              className={`flex items-center gap-2 ${theme.accent} text-[10px] font-bold tracking-wider uppercase`}
            >
              <Info className="h-4 w-4" />
              Travel Advice
            </h3>
            <div className="space-y-2">
              {data.travel_advice.slice(0, 2).map((adv, i) => (
                <div key={i} className="flex gap-2 text-xs text-zinc-500">
                  <div
                    className={`mt-1.5 h-1 w-1 shrink-0 rounded-full bg-current ${theme.accent}`}
                  />
                  {adv}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Triple Column: Packing, Activities */}
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {/* Packing List */}
          <div className={`${theme.bgSubtle} space-y-4 rounded-2xl border border-zinc-100 p-6`}>
            <h3
              className={`flex items-center gap-2 text-[10px] font-bold tracking-wider uppercase ${theme.accent}`}
            >
              <CheckCircle2 className="h-4 w-4" />
              Essential Packing
            </h3>
            <ul className="grid grid-cols-1 gap-2">
              {data.what_to_pack.map((item, i) => (
                <li key={i} className="flex items-center gap-2 text-xs font-bold text-zinc-700">
                  <div className="h-1.5 w-1.5 rounded-full border border-zinc-300 bg-white shadow-sm" />
                  {item}
                </li>
              ))}
            </ul>
          </div>

          {/* Activities */}
          <div className="space-y-4 rounded-2xl border border-zinc-100 p-6">
            <h3 className="flex items-center gap-2 text-[10px] font-bold tracking-wider text-zinc-400 uppercase">
              <Waves className="h-4 w-4" />
              Best Activities
            </h3>
            <div className="flex flex-wrap gap-2">
              {data.suitable_activities.map((act, i) => (
                <span
                  key={i}
                  className="rounded-lg border border-zinc-100 bg-zinc-50 px-3 py-1.5 text-[10px] font-bold text-zinc-500"
                >
                  {act}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between bg-zinc-50 px-8 py-4 text-[9px] font-black tracking-widest text-zinc-400 uppercase italic">
        <span>Dynamic Weather Intelligence</span>
        <div className="flex items-center gap-2">
          <MapPin size={10} />
          {data.destination} in {data.month}
        </div>
      </div>
    </motion.div>
  );
}
