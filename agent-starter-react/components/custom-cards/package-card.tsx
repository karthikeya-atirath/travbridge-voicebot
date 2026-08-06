import React, { useState } from 'react';
import { CheckCircle2, ChevronRight, Hotel, Info, MapPin, XCircle } from 'lucide-react';
import { motion } from 'motion/react';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

export interface PackageItineraryDay {
  day: number;
  description: string;
  mealDescription?: string;
  overnightStay?: string;
}

export interface PackageData {
  packageId: string;
  packageName: string;
  duration: string;
  price: string | number;
  inclusions?: string;
  exclusions?: string;
  imageUrl?: string;
  itinerary?: PackageItineraryDay[];
  images?: string[];
}

interface PackageCardProps {
  pkg: PackageData;
}

type Tier = 'Standard' | 'Value' | 'Premium';

export function PackageCard({ pkg }: PackageCardProps) {
  const [selectedTier, setSelectedTier] = useState<Tier>('Standard');

  // Extract number of nights & days if possible
  const durationString = String(pkg.duration || '');
  const daysMatch = durationString.match(/(\d+)/);
  const daysCount = daysMatch ? parseInt(daysMatch[1]) : 0;
  const nights = daysCount > 1 ? daysCount - 1 : 0;
  const durationText = daysCount ? `${nights} N / ${daysCount} D` : durationString;

  const rawPrice = Number(pkg.price);
  const originalPrice = rawPrice > 0 ? rawPrice + rawPrice * 0.16 : 0;

  const formatINR = (val: number) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(val);

  const images = pkg.images?.length ? pkg.images : pkg.imageUrl ? [pkg.imageUrl] : [];

  const parseList = (htmlOrString?: string) => {
    if (!htmlOrString) return [];
    if (htmlOrString.includes('<li>')) {
      const regex = /<li>(.*?)<\/li>/g;
      const matches = htmlOrString.match(regex);
      if (matches) {
        return matches.map((m) =>
          m
            .replace(/<\/?li>/g, '')
            .replace(/<[^>]+>/g, '')
            .trim()
        );
      }
    }
    return htmlOrString
      .split(/[\n,]/)
      .map((s) => s.trim().replace(/<[^>]+>/g, ''))
      .filter(Boolean)
      .slice(0, 10);
  };

  const inclusionsList = parseList(pkg.inclusions);
  const exclusionsList = parseList(pkg.exclusions);

  const extractHotelForTier = (overnightStay?: string, tier?: Tier) => {
    if (!overnightStay || overnightStay === 'N/A') return null;
    const regex = new RegExp(`([^/\\(]+)\\s*\\(${tier}\\)`, 'i');
    const match = overnightStay.match(regex);
    if (match) return match[1].trim();

    // If no explicit tier markers, and no other tiers mentioned, assume it's for all
    if (
      !overnightStay.includes('(Standard)') &&
      !overnightStay.includes('(Value)') &&
      !overnightStay.includes('(Premium)')
    ) {
      return overnightStay;
    }

    return null;
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95 }}
          whileHover={{ y: -5 }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
          className="group flex h-full min-h-[420px] w-full cursor-pointer flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white text-left shadow-sm transition-all hover:shadow-xl dark:border-zinc-800 dark:bg-zinc-900"
        >
          {/* Top Image Area */}
          <div className="relative h-48 w-full overflow-hidden bg-zinc-100">
            {images.length > 0 ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={images[0]}
                alt=""
                className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-110"
              />
            ) : (
              <div className="absolute inset-0 bg-gradient-to-br from-blue-400 to-indigo-600 opacity-80" />
            )}

            <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent opacity-90" />

            <div className="absolute bottom-4 left-4">
              <Badge className="border-white/10 bg-white/20 px-3 py-1 text-xs font-bold text-white ring-1 ring-white/20 backdrop-blur-md">
                {durationText}
              </Badge>
            </div>
          </div>

          <div className="flex flex-1 flex-col p-6">
            <h3
              className="mb-4 h-[2.5rem] line-clamp-2 text-base font-bold tracking-tight text-neutral-800 dark:text-neutral-100 overflow-hidden"
              style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
            >
              {pkg.packageName}
            </h3>

            {/* Footer: Price */}
            <div className="mt-auto flex items-end justify-between border-t border-zinc-100 pt-5 dark:border-zinc-800">
              <div className="flex flex-col">
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-xs font-bold text-neutral-400 line-through">
                    {formatINR(originalPrice)}
                  </span>
                  <Badge className="h-4 border-none bg-emerald-500 px-1.5 py-0 text-[10px] font-black text-white transition-transform group-hover:scale-110">
                    16% OFF
                  </Badge>
                </div>
                <div className="flex flex-col">
                  <span className="text-2xl font-black tracking-tighter text-neutral-900 dark:text-white">
                    {formatINR(rawPrice)}
                  </span>
                  <span className="text-[10px] font-bold tracking-widest text-neutral-500 uppercase">
                    Starting price
                  </span>
                </div>
              </div>
              <button className="rounded-full bg-[#0a4ca0] p-2.5 text-white shadow-lg transition-all group-hover:bg-[#0d59bb] active:scale-95">
                <ChevronRight className="h-5 w-5" />
              </button>
            </div>
          </div>
        </motion.div>
      </DialogTrigger>

      <DialogContent className="hide-scrollbar max-w-4xl overflow-hidden rounded-[2rem] border-none bg-zinc-50 p-0 dark:bg-zinc-950">
        <DialogTitle className="sr-only">{pkg.packageName}</DialogTitle>
        <ScrollArea className="h-full max-h-[90vh] w-full">
          <div className="flex w-full flex-col">
            {/* Modal Header Image */}
            <div className="relative h-80 w-full bg-zinc-100">
              {images.length > 0 ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={images[0]} alt={pkg.packageName} className="h-full w-full object-cover" />
              ) : (
                <div className="absolute inset-0 bg-gradient-to-br from-blue-600 to-indigo-900" />
              )}
              <div className="absolute inset-0 bg-gradient-to-t from-zinc-50 via-zinc-50/20 to-transparent dark:from-zinc-950 dark:via-zinc-950/20" />

              <div className="absolute right-6 bottom-6 left-6 flex flex-col gap-3">
                <Badge
                  variant="outline"
                  className="w-fit border-indigo-200 bg-indigo-50/50 text-indigo-700 backdrop-blur-md dark:border-indigo-800 dark:bg-indigo-900/50 dark:text-indigo-200"
                >
                  {durationText}
                </Badge>
                <h2 className="text-4xl leading-tight font-black text-neutral-900 dark:text-white">
                  {pkg.packageName}
                </h2>
              </div>
            </div>

            <div className="flex flex-col gap-10 p-6 md:p-10">
              {/* Tier Selection - BEAUTIFIED */}
              <div className="flex flex-col gap-6">
                <div className="flex flex-col gap-2">
                  <h3 className="text-sm font-bold tracking-widest text-neutral-400 uppercase">
                    Select Your Travel Style
                  </h3>
                  <div className="flex w-fit flex-wrap gap-2 rounded-2xl border border-zinc-200 bg-white/50 p-1.5 backdrop-blur-sm dark:border-zinc-800 dark:bg-white/5">
                    {(['Standard', 'Value', 'Premium'] as Tier[]).map((tier) => (
                      <button
                        key={tier}
                        onClick={() => setSelectedTier(tier)}
                        className={cn(
                          'rounded-xl px-6 py-2.5 text-sm font-bold transition-all duration-300',
                          selectedTier === tier
                            ? 'scale-[1.02] bg-[#0a4ca0] text-white shadow-xl shadow-blue-500/20'
                            : 'text-neutral-500 hover:bg-zinc-100 hover:text-neutral-900 dark:hover:bg-zinc-800'
                        )}
                      >
                        {tier}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="flex flex-col gap-4 rounded-3xl border border-blue-100 bg-blue-50/50 p-6 dark:border-blue-900/30 dark:bg-blue-900/10">
                    <div className="flex flex-col">
                      <span className="mb-1 text-xs font-bold tracking-widest text-blue-600/80 uppercase">
                        Pricing for {selectedTier}
                      </span>
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[#0a4ca0]">
                        <span className="text-3xl font-black tracking-tight md:text-4xl">
                          {formatINR(rawPrice)}
                        </span>
                        <span className="text-sm font-bold opacity-70">/ person</span>
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 rounded-3xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900/50">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-zinc-100 dark:bg-zinc-800">
                      <Info className="h-6 w-6 text-zinc-400" />
                    </div>
                    <p className="text-sm leading-tight font-medium text-neutral-500">
                      Price current as of today. May vary based on your specific travel dates.
                    </p>
                  </div>
                </div>
              </div>

              {/* Day-wise Itinerary */}
              {pkg.itinerary && pkg.itinerary.length > 0 && (
                <div className="flex flex-col gap-6">
                  <h3 className="flex items-center gap-3 text-2xl font-black text-neutral-900 dark:text-white">
                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-100 dark:bg-blue-900/30">
                      <MapPin className="h-5 w-5 text-[#0a4ca0]" />
                    </div>
                    Journey Itinerary
                  </h3>

                  <div className="relative ml-5 flex flex-col gap-10 border-l-2 border-zinc-200 pl-10 dark:border-zinc-800">
                    {pkg.itinerary.map((dayPlan, i) => {
                      const hotelName = extractHotelForTier(dayPlan.overnightStay, selectedTier);
                      return (
                        <div key={i} className="group/day relative">
                          <div className="absolute top-0 -left-[51px] flex h-10 w-10 items-center justify-center rounded-2xl border-2 border-zinc-200 bg-zinc-50 text-xs font-black text-neutral-400 ring-8 ring-zinc-50 transition-all group-hover/day:border-blue-500 group-hover/day:text-blue-500 dark:border-zinc-800 dark:bg-zinc-950 dark:ring-zinc-950">
                            D{dayPlan.day}
                          </div>

                          <div className="flex flex-col gap-4">
                            <div className="flex flex-col gap-2">
                              <h4 className="text-lg leading-tight font-bold text-neutral-900 dark:text-white">
                                {dayPlan.overnightStay && dayPlan.overnightStay.includes('Depart')
                                  ? 'Departure'
                                  : `Day ${dayPlan.day}`}
                              </h4>
                              <p
                                className="text-[15px] leading-relaxed text-neutral-600 dark:text-neutral-400"
                                dangerouslySetInnerHTML={{ __html: dayPlan.description }}
                              />
                            </div>

                            {hotelName && (
                              <motion.div
                                initial={{ opacity: 0, x: -10 }}
                                animate={{ opacity: 1, x: 0 }}
                                key={`${i}-${selectedTier}`}
                                className="flex flex-col gap-3 rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm transition-all hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900"
                              >
                                <div className="flex items-center gap-3">
                                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-50 dark:bg-indigo-900/20">
                                    <Hotel className="h-4 w-4 text-indigo-600" />
                                  </div>
                                  <span className="text-[10px] font-black tracking-widest text-indigo-500 uppercase">
                                    Overnight Stay • {selectedTier}
                                  </span>
                                </div>
                                <h5 className="leading-tight font-bold text-neutral-900 dark:text-white">
                                  {hotelName}
                                </h5>
                              </motion.div>
                            )}

                            {dayPlan.mealDescription && dayPlan.mealDescription !== 'N/A' && (
                              <Badge
                                variant="secondary"
                                className="w-fit border-none bg-zinc-100 px-3 py-1 text-[10px] font-bold tracking-wider text-zinc-600 uppercase dark:bg-zinc-800 dark:text-zinc-400"
                              >
                                Meals:{' '}
                                <span
                                  className="ml-1"
                                  dangerouslySetInnerHTML={{ __html: dayPlan.mealDescription }}
                                />
                              </Badge>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Inclusions & Exclusions */}
              <div className="grid grid-cols-1 gap-10 border-t border-zinc-200 pt-10 md:grid-cols-2 dark:border-zinc-800">
                {inclusionsList.length > 0 && (
                  <div className="flex flex-col gap-6">
                    <h3 className="flex items-center gap-3 text-xl font-bold text-emerald-700 dark:text-emerald-400">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-100 dark:bg-emerald-900/30">
                        <CheckCircle2 className="h-5 w-5" />
                      </div>
                      What&apos;s Included
                    </h3>
                    <ul className="flex flex-col gap-4">
                      {inclusionsList.map((inc, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-4 text-[14px] text-neutral-600 dark:text-neutral-400"
                        >
                          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
                          <span
                            className="leading-snug"
                            dangerouslySetInnerHTML={{ __html: inc }}
                          />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {exclusionsList.length > 0 && (
                  <div className="flex flex-col gap-6">
                    <h3 className="flex items-center gap-3 text-xl font-bold text-rose-700 dark:text-rose-400">
                      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-rose-100 dark:bg-rose-900/30">
                        <XCircle className="h-5 w-5" />
                      </div>
                      What&apos;s Not Included
                    </h3>
                    <ul className="flex flex-col gap-4">
                      {exclusionsList.map((exc, i) => (
                        <li
                          key={i}
                          className="flex items-start gap-4 text-[14px] text-neutral-600 dark:text-neutral-400"
                        >
                          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />
                          <span
                            className="leading-snug"
                            dangerouslySetInnerHTML={{ __html: exc }}
                          />
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
