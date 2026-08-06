'use client';

import React, { useState } from 'react';
import {
  ArrowRight,
  Ban,
  Briefcase,
  ChevronDown,
  ChevronLeft,
  ChevronRight as ChevronRightIcon,
  ChevronUp,
  Clock,
  Info,
  Luggage,
  Plane,
  Star,
  Zap,
} from 'lucide-react';
import { AnimatePresence, LayoutGroup, motion } from 'motion/react';

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SegmentItem {
  airline_name: string;
  airline_code: string;
  flight_number: string;
  from_airport_code: string;
  from_airport_name: string;
  to_airport_code: string;
  to_airport_name: string;
  departure_time: string;
  arrival_time: string;
  departure_date: string;
  arrival_date: string;
  duration_text: string;
  aircraft: string;
  operated_by: string;
  baggage_checkin_kg: number | string;
}

interface FareDetails {
  base_fare: string;
  taxes_and_charges: string;
  airline_fare: string;
  discount: string;
  service_fee: string;
  total_fare: string;
  fare_type: string;
  fare_class_type: string;
  currency: string;
}

interface BaggageDetails {
  checkin: string;
  cabin: string;
}

interface CancellationDetails {
  cancellation_fee: string;
  date_change_fee: string;
  thomas_cook_fee: string;
}

export interface FlightOption {
  airline_name: string;
  airline_code: string;
  flight_number: string;
  display_name: string;
  from_city: string;
  to_city: string;
  departure_time: string;
  arrival_time: string;
  departure_date: string;
  arrival_date: string;
  duration_text: string;
  duration_minutes: number;
  stop_count: number;
  stops_text: string;
  layover_text: string;
  price: string;
  currency: string;
  aircraft: string;
  cabin_class: string;
  operated_by: string;
  fare_type: string;
  no_of_seats: string;
  vendor: string;
  recommended: boolean;
  airline_logo_url: string;
  fare_details: FareDetails;
  baggage_details: BaggageDetails;
  cancellation_details: CancellationDetails;
  segments: SegmentItem[];
}

interface AppliedPreference {
  require_non_stop: boolean;
  sort_by: string;
  max_results: number;
}

interface FlightResults {
  trip: string;
  from_city: string;
  to_city: string;
  depart: string;
  custom_input: string;
  applied_preference: AppliedPreference;
  total_found: number;
  flights: FlightOption[];
}

interface FlightMeta {
  noofflights: number;
  nonstopflights: number | any[];
  noOfRecommendedFlight: number;
  memcacheKey: string;
  validFrom: string;
}

export interface DestinationFlightsData {
  flight_results: FlightResults;
  meta: FlightMeta;
  code: number;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatPrice(price: string | number): string {
  // Handle float strings like "11460.0" — drop decimals before stripping non-digits
  const raw = String(price).split('.')[0];
  const num = parseInt(raw.replace(/[^0-9]/g, ''), 10);
  if (isNaN(num)) return '₹ —';
  return `₹ ${num.toLocaleString('en-IN')}`;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '';
  // dateStr is like "01-04-2026"
  const parts = dateStr.split('-');
  if (parts.length !== 3) return dateStr;
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  const day = parseInt(parts[0], 10);
  const monthIdx = parseInt(parts[1], 10) - 1;
  const year = parts[2];
  const weekDays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  try {
    const d = new Date(parseInt(year), monthIdx, day);
    return `${weekDays[d.getDay()]}, ${day} ${months[monthIdx]} ${year}`;
  } catch {
    return dateStr;
  }
}

/* ------------------------------------------------------------------ */
/*  Tab types                                                          */
/* ------------------------------------------------------------------ */

type TabKey = 'flight' | 'fare' | 'baggage' | 'cancellation';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'flight', label: 'Flight' },
  { key: 'fare', label: 'Fare' },
  { key: 'baggage', label: 'Baggage' },
  { key: 'cancellation', label: 'Policies' },
];

/* ------------------------------------------------------------------ */
/*  Single flight card (expandable)                                    */
/* ------------------------------------------------------------------ */

function FlightItem({ flight, index }: { flight: FlightOption; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>('flight');

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.06 * index }}
      className="group w-full overflow-hidden rounded-2xl border border-zinc-200/60 bg-white transition-all duration-300 hover:border-blue-200 hover:shadow-[0_20px_40px_-12px_rgba(0,0,0,0.08)]"
    >
      {/* ── Summary row ───────────────────────────────── */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full px-3 py-3 text-left sm:px-6 sm:py-5"
      >
        {/* Mobile: 2 rows — Row 1: airline + times, Row 2: price */}
        {/* Desktop: single row */}
        <div className="flex items-center gap-2 sm:gap-3">
          {/* Airline */}
          <div className="flex w-12 shrink-0 flex-col items-center gap-0.5 sm:w-24">
            {flight.airline_logo_url ? (
              <img
                src={flight.airline_logo_url}
                alt={flight.airline_code}
                className="h-7 w-7 object-contain sm:h-10 sm:w-10"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : null}
            <span className="w-full truncate text-center text-[7px] font-bold text-zinc-900 uppercase sm:text-[10px]">
              {flight.airline_name}
            </span>
            <span className="text-[7px] font-bold text-zinc-400 sm:text-[9px]">
              {flight.flight_number}
            </span>
          </div>

          {/* Departure */}
          <div className="flex shrink-0 flex-col items-center">
            <span className="text-[13px] font-black tracking-tight text-zinc-900 sm:text-xl">
              {flight.departure_time}
            </span>
            <span className="text-[7px] font-black text-blue-600 uppercase sm:text-[10px]">
              {flight.from_city}
            </span>
          </div>

          {/* Duration line */}
          <div className="flex max-w-[70px] min-w-[40px] flex-1 flex-col items-center gap-0.5 px-0.5 sm:max-w-[100px]">
            <span className="text-center text-[6px] leading-tight font-bold text-zinc-400 uppercase sm:text-[9px]">
              {flight.stops_text}
            </span>
            <div className="h-px w-full bg-zinc-300" />
            <span className="text-center text-[7px] font-medium text-zinc-400 sm:text-[10px]">
              {flight.duration_text}
            </span>
          </div>

          {/* Arrival */}
          <div className="flex shrink-0 flex-col items-center">
            <span className="text-[13px] font-black tracking-tight text-zinc-900 sm:text-xl">
              {flight.arrival_time}
            </span>
            <span className="text-[7px] font-black text-blue-600 uppercase sm:text-[10px]">
              {flight.to_city}
            </span>
          </div>

          {/* Price */}
          <div className="ml-auto flex shrink-0 flex-col items-end">
            <span className="text-[6px] font-bold whitespace-nowrap text-zinc-400 uppercase sm:text-[9px]">
              Per Traveler
            </span>
            <span className="text-[11px] font-black whitespace-nowrap text-zinc-900 sm:text-xl">
              {formatPrice(flight.price)}
            </span>
            {flight.recommended && (
              <Star className="h-2.5 w-2.5 fill-amber-500 text-amber-500 sm:h-3 sm:w-3" />
            )}
          </div>

          {/* Chevron */}
          <motion.div
            animate={{ rotate: expanded ? 180 : 0 }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="shrink-0"
          >
            <ChevronDown className="h-3.5 w-3.5 text-zinc-400 sm:h-4 sm:w-4" />
          </motion.div>
        </div>
      </button>

      {/* ── Expanded tabs ────────────────────────────────── */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden border-t border-zinc-100"
          >
            {/* Tab bar */}
            <div className="scrollbar-hide flex snap-x overflow-x-auto border-b border-zinc-100 bg-zinc-50/50 px-2">
              {TABS.map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`relative min-w-0 flex-1 py-3 text-[9px] font-black tracking-[0.1em] whitespace-nowrap uppercase transition-all duration-300 sm:py-4 sm:text-[11px] sm:tracking-[0.15em] ${
                    activeTab === tab.key ? 'text-blue-600' : 'text-zinc-500 hover:text-zinc-900'
                  }`}
                >
                  {tab.label}
                  {activeTab === tab.key && (
                    <motion.div
                      layoutId="activeTab"
                      className="absolute right-0 bottom-0 left-0 h-[2px] bg-blue-600 shadow-[0_0_8px_rgba(37,99,235,0.2)]"
                    />
                  )}
                </button>
              ))}
            </div>

            <div className="bg-white p-3 sm:p-8">
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                key={activeTab}
                transition={{ duration: 0.3 }}
              >
                {activeTab === 'flight' && <FlightDetailsTab flight={flight} />}
                {activeTab === 'fare' && <FareDetailsTab fare={flight.fare_details} />}
                {activeTab === 'baggage' && (
                  <BaggageDetailsTab
                    baggage={flight.baggage_details}
                    segments={flight.segments}
                    flight={flight}
                  />
                )}
                {activeTab === 'cancellation' && (
                  <CancellationTab cancellation={flight.cancellation_details} />
                )}
              </motion.div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Flight Details                                                */
/* ------------------------------------------------------------------ */

function FlightDetailsTab({ flight }: { flight: FlightOption }) {
  return (
    <div className="space-y-6">
      {/* Route header */}
      <div className="flex items-center justify-between border-b border-zinc-100 pb-4">
        <span className="flex items-center gap-1 text-[11px] font-black tracking-tight text-zinc-900 md:gap-2 md:text-[13px]">
          {flight.from_city} <ArrowRight className="h-3 w-3 text-blue-600 md:h-4 md:w-4" />{' '}
          {flight.to_city}
        </span>
        <div className="flex items-center gap-1.5 text-zinc-400 md:gap-2">
          <Clock className="h-3.5 w-3.5" />
          <span className="text-[11px] font-black tracking-wider uppercase">
            {flight.duration_text} Total
          </span>
        </div>
      </div>

      {/* Segments */}
      <div className="space-y-3">
        {flight.segments.map((seg, idx) => (
          <div key={idx} className="rounded-2xl border border-zinc-100 bg-zinc-50/30 p-3 sm:p-5">
            {/* Airline info */}
            <div className="mb-3 flex items-center gap-2">
              <span className="text-xs font-black text-zinc-900">{seg.airline_name}</span>
              <span className="text-[10px] font-bold text-zinc-400">· {seg.flight_number}</span>
              <span className="ml-auto rounded-full border border-blue-100 bg-blue-50 px-2 py-0.5 text-[8px] font-black text-blue-600 uppercase sm:text-[9px]">
                {flight.cabin_class}
              </span>
            </div>

            {/* Departure → Arrival in a compact horizontal layout */}
            <div className="flex items-start gap-3 sm:gap-6">
              {/* Departure */}
              <div className="flex flex-1 flex-col gap-0.5">
                <span className="text-sm font-black text-zinc-900">
                  {seg.from_airport_code} – {seg.departure_time}
                </span>
                <span className="text-[10px] font-bold text-blue-600/70">
                  {formatDate(seg.departure_date)}
                </span>
                <span className="mt-0.5 text-[9px] leading-snug font-medium text-zinc-500 sm:text-[10px]">
                  {seg.from_airport_name}
                </span>
              </div>

              {/* Divider */}
              <div className="flex shrink-0 flex-col items-center pt-1">
                <div className="h-12 w-px bg-zinc-200" />
              </div>

              {/* Arrival */}
              <div className="flex flex-1 flex-col items-end gap-0.5 text-right">
                <span className="text-sm font-black text-zinc-900">
                  {seg.to_airport_code} – {seg.arrival_time}
                </span>
                <span className="text-[10px] font-bold text-blue-600/70">
                  {formatDate(seg.arrival_date)}
                </span>
                <span className="mt-0.5 text-[9px] leading-snug font-medium text-zinc-500 sm:text-[10px]">
                  {seg.to_airport_name}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Layover info */}
      {flight.stop_count > 0 && flight.layover_text && flight.layover_text !== 'N/A' && (
        <div className="flex items-center gap-3 rounded-xl border border-orange-100 bg-orange-50 px-5 py-3">
          <div className="h-2 w-2 rounded-full bg-orange-500" />
          <span className="text-xs font-black tracking-tight text-orange-700/80">
            Layover Wait: {flight.layover_text}
          </span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Fare Details                                                  */
/* ------------------------------------------------------------------ */

function FareDetailsTab({ fare }: { fare: FareDetails }) {
  const rows = [
    { label: 'Base Fare', value: fare.base_fare },
    { label: 'Airline Charges & Taxes', value: fare.taxes_and_charges },
    { label: 'Airline Fare', value: fare.airline_fare },
    { label: 'Discount', value: fare.discount, prefix: '(-) ', isDiscount: true },
    { label: 'Net Services Fee', value: fare.service_fee },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-zinc-100 pb-4">
        <span className="text-[13px] font-black tracking-tight text-zinc-900 uppercase italic">
          Fare Breakdown
        </span>
        <span className="rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-[10px] font-black text-blue-600">
          {fare.fare_type || 'STANDARD'}
        </span>
      </div>

      <div className="space-y-2">
        {rows.map((row) => (
          <div
            key={row.label}
            className="flex items-center justify-between rounded-lg px-2 py-1 transition-colors hover:bg-zinc-50"
          >
            <span className="text-[11px] font-bold text-zinc-400">{row.label}</span>
            <span
              className={`text-xs font-black ${row.isDiscount ? 'text-emerald-600' : 'text-zinc-700'}`}
            >
              {row.prefix || ''}
              {formatPrice(row.value)}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-4 flex items-center justify-between rounded-2xl bg-blue-600 p-5 shadow-[0_8px_20px_-8px_rgba(37,99,235,0.4)]">
        <div className="flex flex-col">
          <span className="text-[10px] font-black tracking-widest text-white/70 uppercase">
            Total Amount
          </span>
          <span className="text-2xl font-black text-white">{formatPrice(fare.total_fare)}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[9px] font-bold text-white/60">Includes all taxes</span>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Baggage Details                                               */
/* ------------------------------------------------------------------ */

function BaggageDetailsTab({
  baggage,
  segments,
  flight,
}: {
  baggage: BaggageDetails;
  segments: SegmentItem[];
  flight: FlightOption;
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between border-b border-zinc-100 pb-4">
        <span className="flex items-center gap-2 text-[13px] font-black tracking-tight text-zinc-900">
          Allowance Per Traveler
        </span>
        <span className="text-[10px] font-bold tracking-widest text-zinc-400 uppercase">
          {flight.from_city} → {flight.to_city}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:gap-4">
        <div className="group rounded-2xl border border-zinc-100 bg-zinc-50/50 p-3 transition-all hover:border-blue-200 hover:bg-blue-50/50 sm:p-5">
          <div className="mb-2 flex items-center gap-2 sm:mb-4 sm:gap-3">
            <div className="rounded-lg bg-blue-100 p-1.5 text-blue-600 shadow-sm sm:p-2">
              <Luggage className="h-4 w-4 sm:h-5 sm:w-5" />
            </div>
            <span className="text-[9px] font-black tracking-widest text-zinc-500 uppercase sm:text-[11px]">
              Check-in
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-1">
            <span className="text-xl font-black text-zinc-900 sm:text-3xl">
              {(() => {
                const raw =
                  baggage.checkin ||
                  (segments?.[0]?.baggage_checkin_kg ? String(segments[0].baggage_checkin_kg) : '');
                if (!raw || raw === '0') return '—';
                const match = raw.match(/(\d+)\s*kg/i) || raw.match(/^(\d+)/);
                if (match) return match[1];
                return null;
              })()}
            </span>
            {(() => {
              const raw = baggage.checkin || '';
              const isText = raw && !raw.match(/\d/);
              if (isText) {
                return <span className="text-xs font-bold text-zinc-500 sm:text-sm">{raw}</span>;
              }
              return <span className="text-xs font-black text-zinc-400 sm:text-sm">KG</span>;
            })()}
          </div>
        </div>

        <div className="group rounded-2xl border border-zinc-100 bg-zinc-50/50 p-3 transition-all hover:border-purple-200 hover:bg-purple-50/50 sm:p-5">
          <div className="mb-2 flex items-center gap-2 sm:mb-4 sm:gap-3">
            <div className="rounded-lg bg-purple-100 p-1.5 text-purple-600 shadow-sm sm:p-2">
              <Briefcase className="h-4 w-4 sm:h-5 sm:w-5" />
            </div>
            <span className="text-[9px] font-black tracking-widest text-zinc-500 uppercase sm:text-[11px]">
              Cabin
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-1">
            <span className="text-xl font-black text-zinc-900 sm:text-3xl">
              {(() => {
                const raw = baggage.cabin || '7';
                const match = raw.match(/(\d+)\s*kg/i) || raw.match(/^(\d+)/);
                return match ? match[1] : '7';
              })()}
            </span>
            <span className="text-xs font-black text-zinc-400 sm:text-sm">KG</span>
            <span className="ml-0.5 text-[9px] font-bold text-zinc-400 sm:text-[10px]">
              {(() => {
                const raw = baggage.cabin || '';
                const pieceMatch = raw.match(/\((\d+)\s*piece/i);
                return pieceMatch ? `(${pieceMatch[1]} pc)` : '(1 pc)';
              })()}
            </span>
          </div>
        </div>
      </div>

      <div className="flex gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-500" />
        <p className="text-[10px] leading-relaxed font-medium text-zinc-500">
          The baggage allowance provided is as retrieved from the airline reservation system.
          Allowance may vary for connecting flights and airline rule changes.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tab: Cancellation Charges                                          */
/* ------------------------------------------------------------------ */

function CancellationTab({ cancellation }: { cancellation: CancellationDetails }) {
  const hasCancellation =
    cancellation.cancellation_fee || cancellation.date_change_fee || cancellation.thomas_cook_fee;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between border-b border-zinc-100 pb-4">
        <span className="text-[13px] font-black tracking-tight text-zinc-900 uppercase italic">
          Policies & Fees
        </span>
        <span className="text-[9px] font-bold tracking-widest text-zinc-400 uppercase">
          Calculated Per Passenger
        </span>
      </div>

      {hasCancellation ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-6">
          {/* Cancellation fee */}
          <div className="space-y-3">
            <span className="text-[11px] font-black tracking-wider text-zinc-400 uppercase">
              Refund Policy
            </span>
            <div className="space-y-2 rounded-2xl border border-zinc-100 bg-zinc-50/50 p-3 transition-all hover:border-rose-200 hover:bg-rose-50 sm:space-y-3 sm:p-5">
              <div className="flex items-center justify-between gap-2">
                <span className="shrink-0 text-[9px] font-bold text-zinc-400 sm:text-[10px]">
                  Airline Charge
                </span>
                <span className="text-right text-xs font-black text-zinc-900 sm:text-sm">
                  {cancellation.cancellation_fee || '—'}
                </span>
              </div>
              <div className="h-px bg-zinc-100" />
              <div className="flex items-center justify-between gap-2">
                <span className="shrink-0 text-[9px] font-bold text-zinc-400 sm:text-[10px]">
                  Service Fee
                </span>
                <span className="text-right text-xs font-black text-zinc-600 sm:text-sm">
                  {cancellation.thomas_cook_fee || '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Date change fee */}
          <div className="space-y-3">
            <span className="text-[11px] font-black tracking-wider text-zinc-400 uppercase">
              Reschedule Policy
            </span>
            <div className="space-y-2 rounded-2xl border border-zinc-100 bg-zinc-50/50 p-3 transition-all hover:border-emerald-200 hover:bg-emerald-50 sm:space-y-3 sm:p-5">
              <div className="flex items-center justify-between gap-2">
                <span className="shrink-0 text-[9px] font-bold text-zinc-400 sm:text-[10px]">
                  Airline Charge
                </span>
                <span className="text-right text-xs font-black text-zinc-900 sm:text-sm">
                  {cancellation.date_change_fee || '—'}
                </span>
              </div>
              <div className="h-px bg-zinc-100" />
              <div className="flex items-center justify-between gap-2">
                <span className="shrink-0 text-[9px] font-bold text-zinc-400 sm:text-[10px]">
                  Service Fee
                </span>
                <span className="text-right text-xs font-black text-zinc-600 sm:text-sm">
                  {cancellation.thomas_cook_fee || '—'}
                </span>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-4 rounded-2xl border border-zinc-100 bg-zinc-50/50 px-6 py-5">
          <div className="rounded-xl bg-zinc-100 p-3">
            <Ban className="h-6 w-6 text-zinc-400" />
          </div>
          <span className="text-xs leading-relaxed font-medium text-zinc-500">
            Specific cancellation policies are currently unavailable for this flight. Please contact
            support for assistance.
          </span>
        </div>
      )}

      <div className="flex gap-3 rounded-xl border border-zinc-100 bg-zinc-50 p-4">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" />
        <p className="text-[10px] leading-relaxed font-medium text-zinc-400">
          Note: These charges are indicative and subject to change. Airlines stop accepting requests
          4-72 hours before departure. Tax components may be non-refundable for certain fare types.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main card                                                          */
/* ------------------------------------------------------------------ */

interface DestinationFlightsCardProps {
  data: DestinationFlightsData;
}

const FLIGHTS_PER_PAGE = 5;

export function DestinationFlightsCard({ data }: DestinationFlightsCardProps) {
  const results = data?.flight_results;
  const flights = results?.flights || [];
  const [currentPage, setCurrentPage] = useState(0);

  const totalPages = Math.ceil(flights.length / FLIGHTS_PER_PAGE);
  const startIdx = currentPage * FLIGHTS_PER_PAGE;
  const visibleFlights = flights.slice(startIdx, startIdx + FLIGHTS_PER_PAGE);

  if (!flights.length) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="box-border flex w-fit max-w-[800px] min-w-0 flex-col overflow-hidden rounded-[32px] border border-zinc-200 bg-white/90 shadow-2xl backdrop-blur-2xl"
      >
        <div className="relative overflow-hidden px-8 py-12 text-zinc-900">
          <div className="absolute -top-24 -right-24 h-64 w-64 rounded-full bg-blue-100/50 blur-[100px]" />
          <div className="absolute -bottom-24 -left-24 h-64 w-64 rounded-full bg-purple-100/30 blur-[100px]" />

          <div className="relative z-10 flex flex-col items-center space-y-4 text-center">
            <div className="flex w-fit items-center gap-2 rounded-full border border-zinc-200 bg-zinc-100 px-4 py-1.5 text-[11px] font-black tracking-[0.2em] uppercase backdrop-blur-md">
              Flight Search
            </div>
            <h2 className="text-4xl font-black tracking-tighter text-zinc-900">No Flights Found</h2>
            <p className="max-w-md text-sm leading-relaxed font-medium text-zinc-500">
              We couldn't find any flights for{' '}
              <span className="font-bold text-zinc-900">
                {results?.from_city} → {results?.to_city}
              </span>{' '}
              on <span className="font-bold text-zinc-900">{results?.depart}</span>. Try adjusting
              your search criteria.
            </p>
          </div>
        </div>
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full min-w-0 max-w-full flex-col overflow-hidden rounded-[32px] border border-zinc-200/50 bg-slate-50/90 shadow-[0_32px_128px_-16px_rgba(0,0,0,0.1)] backdrop-blur-2xl md:max-w-[600px]"
    >
      {/* Header */}
      <div className="relative overflow-hidden px-4 pt-12 pb-10 text-zinc-900 md:px-8">
        {/* Decorative elements */}
        <div className="absolute -top-24 -right-24 h-80 w-80 rounded-full bg-blue-100/50 blur-[120px]" />
        <div className="absolute -bottom-24 -left-24 h-64 w-64 rounded-full bg-purple-100/30 blur-[100px]" />

        <div className="relative z-10 space-y-6">
          <div className="flex w-fit items-center gap-2 rounded-full border border-zinc-200 bg-white/40 px-4 py-1.5 text-[11px] font-black tracking-[0.2em] uppercase backdrop-blur-md transition-colors hover:border-blue-200">
            {results.trip === 'dom' ? 'Domestic' : 'International'} Search
          </div>

          <div className="space-y-1">
            <h2 className="flex flex-wrap items-center text-2xl font-black tracking-tighter text-zinc-900 sm:text-4xl md:text-5xl">
              <span>{results.from_city}</span>
              <ArrowRight className="mx-1 h-5 w-5 text-blue-600/30 md:mx-2 md:h-8 md:w-8" />
              <span>{results.to_city}</span>
            </h2>
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-zinc-400" />
                <span className="text-[13px] font-bold text-zinc-500">
                  {formatDate(results.depart)}
                </span>
              </div>
              <div className="h-1 w-1 rounded-full bg-zinc-300" />
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-blue-200 bg-blue-100 px-3 py-1 text-[11px] font-black tracking-wide text-blue-600">
                  {results.total_found} FLIGHTS
                </span>
              </div>
              {results.custom_input && (
                <div className="flex items-center gap-2">
                  <div className="h-1 w-1 rounded-full bg-zinc-300" />
                  <span className="max-w-[100px] truncate rounded-full border border-purple-200 bg-purple-100 px-2 py-1 text-[9px] font-black tracking-wide text-purple-600 uppercase md:max-w-none md:px-3 md:text-[11px]">
                    {results.custom_input}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Flight list (paginated) */}
      <div className="flex flex-col gap-4 px-4 py-10 pt-4 md:gap-6 md:px-6">
        {visibleFlights.map((flight, idx) => (
          <FlightItem
            key={`${flight.flight_number}-${startIdx + idx}`}
            flight={flight}
            index={idx}
          />
        ))}
      </div>

      {/* Pagination controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 border-t border-zinc-100 px-6 py-4">
          <button
            onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
            disabled={currentPage === 0}
            className="flex items-center gap-1 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-[10px] font-black tracking-wider text-zinc-600 uppercase shadow-sm transition-all hover:border-blue-200 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
            Prev
          </button>
          <div className="flex items-center gap-1.5">
            {Array.from({ length: totalPages }, (_, i) => (
              <button
                key={i}
                onClick={() => setCurrentPage(i)}
                className={`h-7 w-7 rounded-full text-[10px] font-black transition-all ${
                  currentPage === i
                    ? 'bg-blue-600 text-white shadow-[0_4px_12px_-2px_rgba(37,99,235,0.4)]'
                    : 'border border-zinc-200 bg-white text-zinc-500 hover:border-blue-200 hover:text-blue-600'
                }`}
              >
                {i + 1}
              </button>
            ))}
          </div>
          <button
            onClick={() => setCurrentPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={currentPage === totalPages - 1}
            className="flex items-center gap-1 rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-[10px] font-black tracking-wider text-zinc-600 uppercase shadow-sm transition-all hover:border-blue-200 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-30"
          >
            Next
            <ChevronRightIcon className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Footer */}
      <div className="flex flex-col items-center justify-between gap-4 border-t border-zinc-100 bg-white/20 px-6 py-6 sm:flex-row md:px-8">
        <span className="text-[10px] font-black tracking-[0.3em] text-zinc-400 uppercase italic">
          Powered by Thomas Cook
        </span>
        <span className="text-[10px] font-bold tracking-wide text-zinc-400">
          Showing {startIdx + 1}–{Math.min(startIdx + FLIGHTS_PER_PAGE, flights.length)} of{' '}
          {flights.length} results
        </span>
      </div>
    </motion.div>
  );
}
