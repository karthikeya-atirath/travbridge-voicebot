/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useRef } from 'react';
import { AlertCircle, CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { motion } from 'motion/react';

interface FareCalendarCardProps {
  data: any;
}

export function FareCalendarCard({ data }: FareCalendarCardProps) {
  const payload = data?.itinerary_data || data?.body || data?.data || data;

  let bookableDatesObj: any = {};
  let packageInfo: any = {};

  // Parse the TravBridge direct API format
  if (payload && typeof payload === 'object') {
    const keys = Object.keys(payload).filter((k) => k.startsWith('PKG'));
    if (keys.length > 0) {
      const pkgData = payload[keys[0]];
      if (pkgData?.dates?.bookable) {
        bookableDatesObj = pkgData.dates.bookable;
      }
      if (pkgData?.package) {
        packageInfo = pkgData.package;
      }
    } else {
      // Fallback for LLM structured output or older formats
      const rawFare = data?.fareCalendar || payload?.fareCalendar || data;
      if (rawFare?.departureCities?.[0]?.dates?.bookable) {
        bookableDatesObj = rawFare.departureCities[0].dates.bookable;
      } else if (rawFare?.classTypes?.[0]?.dates?.bookable) {
        bookableDatesObj = rawFare.classTypes[0].dates.bookable;
      } else if (rawFare?.dates?.bookable) {
        bookableDatesObj = rawFare.dates.bookable;
      } else if (rawFare?.bookable) {
        bookableDatesObj = rawFare.bookable;
      }
    }
  }

  const bookableDates: { date: string; price: number }[] = [];

  // Parse if it's the YYYY-MM -> DD object format
  if (typeof bookableDatesObj === 'object' && !Array.isArray(bookableDatesObj)) {
    Object.keys(bookableDatesObj).forEach((yearMonth) => {
      const daysObj = bookableDatesObj[yearMonth];
      if (typeof daysObj === 'object') {
        Object.keys(daysObj).forEach((day) => {
          const price = daysObj[day]?.p || daysObj[day]?.price || 0;
          bookableDates.push({
            date: `${yearMonth}-${day.padStart(2, '0')}`,
            price,
          });
        });
      }
    });
  } else if (Array.isArray(bookableDatesObj)) {
    bookableDatesObj.forEach((d: any) => {
      if (d.date) {
        // Assume format "DD-MM-YYYY" or "YYYY-MM-DD"
        const parts = d.date.split('-');
        let formattedDate = d.date;
        if (parts.length === 3 && parts[2].length === 4) {
          formattedDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
        }
        bookableDates.push({
          date: formattedDate,
          price: d.price || d.p || 0,
        });
      }
    });
  }

  // Sort dates chronologically
  bookableDates.sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime());

  const title =
    packageInfo?.packageName ||
    payload?.packageName ||
    payload?.package_name ||
    'Departure Calendar';
  const summary =
    payload?.calendarSummary ||
    payload?.calendar_summary ||
    'Here are the upcoming departures and prices for this package.';
  const scrollRef = useRef<HTMLDivElement>(null);

  const scrollDates = (direction: 'left' | 'right') => {
    if (scrollRef.current) {
      const scrollAmount = direction === 'left' ? -200 : 200;
      scrollRef.current.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    }
  };

  const formatPrice = (price: number) => {
    if (!price) return '';
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(price);
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95, y: 15 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration: 0.4, ease: 'easeOut' }}
      className="my-4 w-full max-w-full rounded-2xl border border-neutral-200 bg-white/90 p-5 shadow-sm backdrop-blur-md md:max-w-[600px] dark:border-neutral-800 dark:bg-zinc-900/90"
    >
      <div className="mb-4 flex flex-col justify-between gap-3 md:flex-row md:items-center">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-blue-50 p-2.5 text-blue-600 dark:bg-blue-900/30">
            <CalendarDays className="h-6 w-6" />
          </div>
          <div>
            <h3 className="line-clamp-1 text-lg font-bold text-neutral-900 dark:text-white">
              {title}
            </h3>
            <p className="text-xs font-medium text-neutral-500">Available Departures</p>
          </div>
        </div>
      </div>

      <div className="mb-4 rounded-xl bg-neutral-50/80 p-4 text-sm leading-relaxed font-medium text-neutral-700 dark:bg-zinc-800/80 dark:text-neutral-300">
        {summary}
      </div>

      {bookableDates.length > 0 ? (
        <div className="group relative flex items-center">
          <button
            onClick={() => scrollDates('left')}
            className="absolute -left-3 z-10 hidden h-8 w-8 items-center justify-center rounded-full border border-neutral-200 bg-white opacity-0 shadow-sm transition-opacity group-hover:opacity-100 md:flex dark:border-zinc-700 dark:bg-zinc-800"
          >
            <ChevronLeft className="h-4 w-4 text-neutral-600 dark:text-neutral-300" />
          </button>

          <div
            ref={scrollRef}
            className="hide-scrollbar flex w-full snap-x snap-mandatory gap-3 overflow-x-auto px-1 py-2"
          >
            {bookableDates.map((d: { date: string; price: number }, i: number) => {
              const dateObj = new Date(d.date);
              const monthStr = dateObj.toLocaleDateString('en-US', { month: 'short' });
              const dayStr = dateObj.getDate();
              const yearStr = dateObj.getFullYear();
              const weekdayStr = dateObj.toLocaleDateString('en-US', { weekday: 'short' });

              return (
                <div
                  key={i}
                  className="flex min-w-[90px] shrink-0 cursor-pointer snap-center flex-col items-center justify-center gap-1 rounded-xl border border-blue-100 bg-white p-3 shadow-sm transition-all hover:border-blue-300 hover:shadow-md dark:border-blue-900/50 dark:bg-zinc-800"
                >
                  <span className="text-[10px] font-bold tracking-widest text-neutral-400 uppercase">
                    {weekdayStr}
                  </span>
                  <div className="flex items-baseline gap-1">
                    <span className="text-2xl font-black text-neutral-900 dark:text-white">
                      {dayStr}
                    </span>
                    <span className="text-sm font-bold text-blue-600 dark:text-blue-400">
                      {monthStr}
                    </span>
                  </div>
                  <span className="text-[10px] font-medium text-neutral-400">{yearStr}</span>
                  {d.price > 0 && (
                    <div className="mt-2 flex w-full items-center justify-center rounded-md bg-green-50 px-2 py-1 text-xs font-bold text-green-700 dark:bg-green-900/30 dark:text-green-400">
                      {formatPrice(d.price)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <button
            onClick={() => scrollDates('right')}
            className="absolute -right-3 z-10 hidden h-8 w-8 items-center justify-center rounded-full border border-neutral-200 bg-white opacity-0 shadow-sm transition-opacity group-hover:opacity-100 md:flex dark:border-zinc-700 dark:bg-zinc-800"
          >
            <ChevronRight className="h-4 w-4 text-neutral-600 dark:text-neutral-300" />
          </button>
        </div>
      ) : (
        <div className="flex divide-x divide-neutral-200 dark:divide-zinc-700/50">
          <div className="flex items-start gap-2 px-1 pt-2">
            <AlertCircle className="mt-0.5 h-4 w-4 text-orange-400" />
            <p className="text-xs font-medium text-neutral-500 italic">
              No specific dates available to display.
            </p>
          </div>
        </div>
      )}
    </motion.div>
  );
}
