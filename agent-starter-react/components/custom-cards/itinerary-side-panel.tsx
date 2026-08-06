'use client';

import React from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  Calendar,
  Car,
  ChevronDown,
  ChevronUp,
  Clock,
  Download,
  Eye,
  Hotel,
  Lightbulb,
  MapPin,
  Plane,
  Star,
  Sunrise,
  Utensils,
  Users,
  Wallet,
  X,
  Zap,
} from 'lucide-react';
import { useItineraryPanel } from '@/hooks/use-itinerary-panel';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/shadcn/utils';

/* ═══════════════════════════════════════════════════════════════════════════
   Types
   ═══════════════════════════════════════════════════════════════════════════ */

interface FlightInfo {
  airline_name?: string;
  flight_number?: string;
  from_city?: string;
  to_city?: string;
  departure_time?: string;
  arrival_time?: string;
  duration_text?: string;
  price?: string;
  currency?: string;
  stops_text?: string;
  airline_logo_url?: string;
}

interface HotelInfo {
  name?: string;
  star_rating?: number;
  address?: string;
  description?: string;
  image_url?: string;
}

interface SightseeingItem {
  name?: string;
  description?: string;
  ideal_duration?: string;
  best_time?: string;
  image_url?: string;
  is_must_do?: boolean;
}

interface ActivityItem {
  name?: string;
  description?: string;
  category?: string;
  ideal_duration?: string;
  image_url?: string;
  is_must_do?: boolean;
}

interface TransferItem {
  transfer_type?: string;
  from_location?: string;
  to_location?: string;
  mode?: string;
  estimated_duration?: string;
  notes?: string;
}

interface MealInfo {
  breakfast?: string;
  lunch?: string;
  dinner?: string;
}

interface DayPlan {
  day?: number;
  date?: string;
  title?: string;
  city?: string;
  summary?: string;
  flight?: FlightInfo;
  hotel?: HotelInfo;
  sightseeing?: SightseeingItem[];
  activities?: ActivityItem[];
  transfers?: TransferItem[];
  meals?: MealInfo;
  notes?: string;
}

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
  days?: DayPlan[];
  total_estimated_budget?: string;
  budget_breakdown?: string[];
  tips?: string[];
  hero_image_url?: string;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════════════════════════════════════ */

const StarsRow = ({ count }: { count: number }) => (
  <div className="flex gap-0.5">
    {Array.from({ length: Math.min(count || 0, 5) }).map((_, i) => (
      <Star key={i} className="h-3 w-3 fill-amber-400 text-amber-400" />
    ))}
  </div>
);

function MealBadge({ meals }: { meals: MealInfo }) {
  const items = [
    { label: 'Breakfast', val: meals.breakfast },
    { label: 'Lunch', val: meals.lunch },
    { label: 'Dinner', val: meals.dinner },
  ].filter((m) => m.val);
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Utensils className="h-3 w-3 text-rose-400" />
      {items.map((m, i) => (
        <span key={i} className="rounded-full bg-rose-50 px-2 py-0.5 text-[9px] font-bold text-rose-600 dark:bg-rose-900/20 dark:text-rose-400" title={m.val}>
          {m.label}: {m.val}
        </span>
      ))}
    </div>
  );
}

function TransferRow({ item }: { item: TransferItem }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-zinc-100 bg-zinc-50/80 px-3 py-2 text-[10px] font-medium text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/40 dark:text-zinc-400">
      <Car className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{item.from_location} → {item.to_location}</span>
      {item.estimated_duration && <span className="ml-auto shrink-0 font-bold text-zinc-400">~ {item.estimated_duration}</span>}
    </div>
  );
}

function PremiumCard({
  icon: Icon,
  title,
  subtitle,
  details,
  badge,
  image,
  accentColor = "indigo",
  price
}: {
  icon: any,
  title: string,
  subtitle?: string,
  details?: React.ReactNode,
  badge?: string,
  image?: string,
  accentColor?: string,
  price?: string
}) {
  const accentClasses: Record<string, string> = {
    indigo: "border-indigo-500/30 text-indigo-600 dark:text-indigo-400 bg-indigo-50/50 dark:bg-indigo-950/20",
    sky: "border-sky-500/30 text-sky-600 dark:text-sky-400 bg-sky-50/50 dark:bg-sky-950/20",
    emerald: "border-emerald-500/30 text-emerald-600 dark:text-emerald-400 bg-emerald-50/50 dark:bg-indigo-950/20",
    purple: "border-purple-500/30 text-purple-600 dark:text-purple-400 bg-purple-50/50 dark:bg-purple-950/20",
    orange: "border-orange-500/30 text-orange-600 dark:text-orange-400 bg-orange-50/50 dark:bg-orange-950/20",
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        "group relative flex gap-4 rounded-2xl border border-white/20 bg-white/40 p-4 shadow-sm backdrop-blur-md transition-all hover:border-white/40 hover:bg-white/60 dark:border-white/5 dark:bg-white/5 dark:hover:bg-white/10",
        `border-l-4 ${accentClasses[accentColor].split(' ')[0]}`
      )}
    >
      {image && (
        <div className="h-16 w-16 shrink-0 overflow-hidden rounded-xl bg-zinc-100 dark:bg-zinc-800">
          <img src={image} alt="" className="h-full w-full object-cover" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <div className={cn("flex h-6 w-6 items-center justify-center rounded-lg", accentClasses[accentColor])}>
                <Icon className="h-3.5 w-3.5" />
              </div>
              <h4 className="text-[13px] font-black tracking-tight text-zinc-900 dark:text-zinc-100 uppercase">{title}</h4>
              {badge && (
                <span className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-[9px] font-black text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 uppercase tracking-widest">
                  {badge}
                </span>
              )}
            </div>
            {subtitle && <p className="text-[11px] font-bold text-zinc-500 dark:text-zinc-400 line-clamp-1">{subtitle}</p>}
          </div>
          {price && (
            <div className="text-right shrink-0">
              <span className="text-[13px] font-black text-indigo-700 dark:text-indigo-400">{price}</span>
            </div>
          )}
        </div>
        {details && <div className="mt-3">{details}</div>}
      </div>
    </motion.div>
  );
}

function FlightRow({ flight }: { flight: FlightInfo }) {
  return (
    <PremiumCard
      icon={Plane}
      accentColor="sky"
      title={flight.airline_name || 'Flight'}
      subtitle={`${flight.from_city} → ${flight.to_city}`}
      badge={flight.flight_number}
      price={flight.price ? `₹${Number(flight.price).toLocaleString('en-IN')}` : undefined}
      details={
        <div className="flex flex-wrap items-center gap-3 text-[10px] font-black text-zinc-400 uppercase tracking-tighter">
          {flight.departure_time && <span>✈ {flight.departure_time} – {flight.arrival_time}</span>}
          {flight.duration_text && <span className="flex items-center gap-1"><Clock size={12}/>{flight.duration_text}</span>}
          {flight.stops_text && <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[8px] text-sky-600 dark:bg-sky-900/30 dark:text-sky-400">{flight.stops_text}</span>}
        </div>
      }
    />
  );
}

function HotelRow({ hotel }: { hotel: HotelInfo }) {
  return (
    <PremiumCard
      icon={Hotel}
      accentColor="emerald"
      image={hotel.image_url}
      title={hotel.name || 'Premium Stay'}
      subtitle={hotel.address}
      details={
        <div className="flex flex-col gap-2">
           {hotel.star_rating && <StarsRow count={hotel.star_rating} />}
           {hotel.description && <p className="text-[11px] font-medium leading-relaxed text-zinc-500 line-clamp-2">{hotel.description}</p>}
        </div>
      }
    />
  );
}

function SightseeingRow({ item }: { item: SightseeingItem }) {
  return (
    <PremiumCard
      icon={Eye}
      accentColor="purple"
      image={item.image_url}
      title={item.name || 'Sightseeing'}
      subtitle={item.description}
      badge={item.is_must_do ? "Must Do" : undefined}
      details={
        <div className="flex items-center gap-4 text-[10px] font-black text-zinc-400 uppercase tracking-widest">
           {item.ideal_duration && <span className="flex items-center gap-1.5"><Clock size={12} className="text-zinc-500"/>{item.ideal_duration}</span>}
           {item.best_time && <span className="flex items-center gap-1.5"><Sunrise size={12} className="text-zinc-500"/>{item.best_time}</span>}
        </div>
      }
    />
  );
}

function ActivityRow({ item }: { item: ActivityItem }) {
  return (
    <PremiumCard
      icon={Zap}
      accentColor="orange"
      image={item.image_url}
      title={item.name || 'Activity'}
      subtitle={item.description}
      badge={item.category}
      details={item.ideal_duration ? <span className="text-[10px] font-black text-zinc-400 uppercase tracking-widest"><Clock size={12} className="inline mr-1.5"/>{item.ideal_duration}</span> : undefined}
    />
  );
}

function DaySection({ day, isFirst, isLast }: { day: DayPlan; isFirst: boolean; isLast: boolean }) {
  const [open, setOpen] = React.useState(isFirst);

  const hasFlight = day.flight && day.flight.airline_name;
  const hasHotel = day.hotel && day.hotel.name;
  const hasSightseeing = day.sightseeing && day.sightseeing.length > 0;
  const hasActivities = day.activities && day.activities.length > 0;
  const hasTransfers = day.transfers && day.transfers.length > 0;
  const hasMeals = day.meals && (day.meals.breakfast || day.meals.lunch || day.meals.dinner);

  // Main Image for the day (Sightseeing or Hotel)
  const mainImage = day.sightseeing?.[0]?.image_url || day.hotel?.image_url;

  return (
    <div className="relative pl-12 pb-8 last:pb-4">
      {!isLast && (
        <div className="absolute left-5 top-12 bottom-0 w-[3px] rounded-full bg-linear-to-b from-indigo-500/40 via-indigo-500/10 to-transparent" />
      )}

      <div className="absolute left-0 top-0 z-20 flex h-10 w-10 items-center justify-center rounded-full border-[3px] border-white bg-indigo-600 font-black text-white shadow-xl shadow-indigo-600/30 dark:border-zinc-900">
        <span className="text-[12px]">D{day.day}</span>
      </div>

      <div className="group flex flex-col gap-3">
        <button
          onClick={() => setOpen(!open)}
          className="flex w-full flex-col gap-1 text-left"
        >
          <div className="flex items-center justify-between">
             <div className="space-y-0.5">
               <h3 className="text-lg font-black tracking-tighter text-zinc-900 dark:text-white lg:text-xl">
                 {day.title || `Day ${day.day}`}
               </h3>
               <div className="flex items-center gap-3 text-[11px] font-black uppercase tracking-widest text-zinc-400">
                 {day.city && <span className="flex items-center gap-1"><MapPin size={14} className="text-indigo-500"/>{day.city}</span>}
                 {day.date && <span className="flex items-center gap-1"><Calendar size={14} className="text-indigo-500"/>{day.date}</span>}
               </div>
             </div>
             <div className={cn("rounded-full bg-zinc-50 p-2 shadow-sm transition-all group-hover:bg-indigo-50 dark:bg-white/5 dark:group-hover:bg-indigo-950/20", open ? "text-indigo-600" : "text-zinc-400")}>
               {open ? <ChevronUp size={18}/> : <ChevronDown size={18}/>}
             </div>
          </div>
        </button>

        <AnimatePresence>
          {open && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
              className="overflow-hidden"
            >
              <div className="space-y-4 pt-2">
                {mainImage && (
                  <motion.div
                    initial={{ scale: 0.95, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    className="relative h-36 w-full overflow-hidden rounded-2xl group/img"
                  >
                    <img src={mainImage} alt="" className="h-full w-full object-cover transition-transform duration-1000 group-hover/img:scale-110" />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
                    <div className="absolute bottom-3 left-4">
                       <span className="rounded-full bg-white/20 px-3 py-1 text-[10px] font-black uppercase tracking-[0.2em] text-white backdrop-blur-md">
                         Daily Highlight
                       </span>
                    </div>
                  </motion.div>
                )}

                {day.summary && (
                  <div className="rounded-2xl border border-indigo-100 bg-indigo-50/30 p-4 text-[12px] font-bold leading-relaxed text-zinc-600 dark:border-indigo-900/20 dark:bg-indigo-950/10 dark:text-zinc-400">
                    {day.summary}
                  </div>
                )}

                <div className="grid grid-cols-1 gap-3">
                  {hasFlight && <FlightRow flight={day.flight!} />}
                  {(hasSightseeing || hasActivities) && (
                    <div className="space-y-3">
                      {day.sightseeing?.map((s, i) => <SightseeingRow key={`s-${i}`} item={s} />)}
                      {day.activities?.map((a, i) => <ActivityRow key={`a-${i}`} item={a} />)}
                    </div>
                  )}
                  {hasHotel && <HotelRow hotel={day.hotel!} />}
                  {hasTransfers && (
                    <div className="space-y-2">
                      {day.transfers?.map((t, i) => <TransferRow key={`t-${i}`} item={t} />)}
                    </div>
                  )}
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3">
                   {hasMeals && <MealBadge meals={day.meals!} />}
                   {day.notes && (
                      <div className="flex items-center gap-2 rounded-2xl bg-amber-500/10 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest text-amber-700 dark:text-amber-400">
                        <Lightbulb size={14}/>
                        <span>Tip</span>
                      </div>
                   )}
                </div>

                {day.notes && (
                  <p className="pl-4 text-[11px] font-bold italic leading-relaxed text-zinc-400 border-l-2 border-amber-500/30">
                    &quot;{day.notes}&quot;
                  </p>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Reusable Inner Panel Content
   ═══════════════════════════════════════════════════════════════════════════ */

function ItineraryPanelContent({ data, closePanel }: { data: ItineraryData, closePanel: () => void }) {
  const [showForm, setShowForm] = React.useState(false);
  const days = data.days || [];
  const totalTravellers = (data.adults || 0) + (data.children || 0) + (data.infants || 0);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-white dark:bg-zinc-950">
      {/* COMPACT HERO SECTION */}
      <div className="relative shrink-0 h-[200px] w-full items-end overflow-hidden flex">
        {data.hero_image_url ? (
          <img src={data.hero_image_url} alt="" className="absolute inset-0 h-full w-full object-cover scale-105" />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-br from-indigo-900 via-purple-950 to-black" />
        )}
        <div className="absolute inset-x-0 bottom-0 h-2/3 bg-gradient-to-t from-zinc-950 via-zinc-950/60 to-transparent" />
        <div className="absolute inset-0 bg-black/10" />

        <div className="absolute top-4 right-4 z-30">
          <button
            onClick={closePanel}
            className="rounded-full bg-white/20 p-2.5 text-white shadow-2xl backdrop-blur-xl transition-all hover:bg-white/30 hover:scale-110 active:scale-95"
          >
            <X size={20}/>
          </button>
        </div>

        <div className="relative z-20 w-full px-6 pb-4 space-y-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              {data.trip_type && (
                <span className="rounded-full bg-white/10 px-3 py-1 text-[9px] font-black uppercase tracking-[0.15em] text-zinc-300 backdrop-blur-md">
                  {data.trip_type}
                </span>
              )}
            </div>
            <h2 className="text-2xl font-black tracking-tighter text-white lg:text-3xl leading-[0.95]">
              {data.destination || 'Custom Escape'}
            </h2>
            <p className="text-xs font-bold text-zinc-400 line-clamp-1">{data.title || "Your curated travel itinerary."}</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-1.5 rounded-xl bg-white/10 px-3 py-1.5 text-[10px] font-black text-white uppercase backdrop-blur-md">
              <Calendar size={13} className="text-indigo-400"/>
              {data.start_date || 'TBD'} — {data.end_date || 'TBD'}
            </div>
            <div className="flex items-center gap-1.5 rounded-xl bg-white/10 px-3 py-1.5 text-[10px] font-black text-white uppercase backdrop-blur-md">
              <Users size={13} className="text-indigo-400"/>
              {totalTravellers || 0}
            </div>
            {data.total_estimated_budget && (
              <div className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3 py-1.5 text-[10px] font-black text-white uppercase shadow-lg shadow-indigo-600/30">
                <Wallet size={13}/>
                {data.total_estimated_budget}
              </div>
            )}
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1 overflow-hidden h-full">
        <div className="px-6 py-6">
          <div className="space-y-2 mb-8">
            <h4 className="text-[10px] font-black uppercase tracking-[0.3em] text-zinc-400">Your Plan</h4>
            <p className="text-sm font-bold leading-snug text-zinc-600 dark:text-zinc-300">
              A {days.length}-day journey to {data.destination || "your destination"} from {data.from_city || "your city"}.
            </p>
          </div>

          <div className="space-y-2">
            {days.map((day, idx) => (
              <DaySection
                key={day.day || idx}
                day={day}
                isFirst={idx === 0}
                isLast={idx === days.length - 1}
              />
            ))}
          </div>

          {data.tips && data.tips.length > 0 && (
            <div className="mt-10 rounded-3xl border border-white/10 bg-zinc-50 dark:bg-white/5 p-6">
              <div className="flex items-center gap-3 mb-5">
                <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500/20 text-amber-600">
                  <Lightbulb size={20}/>
                </div>
                <div>
                  <h4 className="text-[10px] font-black uppercase tracking-widest text-zinc-400 mb-0.5">Insider Knowledge</h4>
                  <h3 className="text-base font-black text-zinc-900 dark:text-white">Pro Travel Tips</h3>
                </div>
              </div>
              <ul className="space-y-3">
                {data.tips.map((tip, i) => (
                  <li key={i} className="flex gap-3 items-start">
                    <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />
                    <p className="text-[12px] font-bold leading-relaxed text-zinc-600 dark:text-zinc-400">{tip}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <AnimatePresence>
            {showForm && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                className="overflow-hidden"
              >
                <div className="mt-6 rounded-3xl border border-indigo-100 bg-indigo-50/30 p-6 dark:border-indigo-900/20 dark:bg-indigo-950/10">
                  <h4 className="text-sm font-black text-zinc-900 dark:text-white mb-4">Your Details</h4>
                  <div className="space-y-3">
                    <input type="text" placeholder="Full Name" className="w-full rounded-xl border border-zinc-200 bg-white px-4 py-3 text-sm font-medium dark:bg-zinc-900 dark:border-zinc-700" />
                    <input type="email" placeholder="Email Address" className="w-full rounded-xl border border-zinc-200 bg-white px-4 py-3 text-sm font-medium dark:bg-zinc-900 dark:border-zinc-700" />
                    <input type="tel" placeholder="Phone Number" className="w-full rounded-xl border border-zinc-200 bg-white px-4 py-3 text-sm font-medium dark:bg-zinc-900 dark:border-zinc-700" />
                    <button className="w-full rounded-xl bg-indigo-600 px-6 py-3 text-sm font-black text-white shadow-lg">Submit & Download PDF</button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </ScrollArea>

      <div className="shrink-0 px-6 py-4 border-t border-zinc-100 dark:border-white/5 flex items-center justify-end">
        <button
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-2 rounded-2xl bg-indigo-600 px-6 py-3 text-[11px] font-black uppercase tracking-widest text-white shadow-xl"
        >
          <Download size={16} />
          Download Your Itinerary
        </button>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   DESKTOP VERSION (Static in-flow)
   ═══════════════════════════════════════════════════════════════════════════ */

export function ItinerarySidePanelDesktop() {
  const { isOpen, activeItinerary, closePanel } = useItineraryPanel();
  const data = activeItinerary?.data || {};

  if (!isOpen || !activeItinerary) return null;

  return (
    <div className="h-full w-full">
      <ItineraryPanelContent data={data} closePanel={closePanel} />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   MOBILE VERSION (Fixed overlay)
   ═══════════════════════════════════════════════════════════════════════════ */

export function ItinerarySidePanelMobile() {
  const { isOpen, activeItinerary, closePanel } = useItineraryPanel();
  const data = activeItinerary?.data || {};

  return (
    <AnimatePresence>
      {isOpen && activeItinerary && (
        <div className="fixed inset-0 z-50 md:hidden">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={closePanel}
            className="absolute inset-0 bg-zinc-950/60 backdrop-blur-sm"
          />
          <motion.aside
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 250 }}
            className="absolute inset-y-0 right-0 flex h-full w-full flex-col shadow-2xl"
          >
            <ItineraryPanelContent data={data} closePanel={closePanel} />
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Default Export (for backward compatibility if needed)
   ═══════════════════════════════════════════════════════════════════════════ */

export function ItinerarySidePanel() {
  return (
    <>
      <div className="hidden md:block">
        <ItinerarySidePanelDesktop />
      </div>
      <div className="md:hidden">
        <ItinerarySidePanelMobile />
      </div>
    </>
  );
}
