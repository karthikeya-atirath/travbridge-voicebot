'use client';

import React from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  FileText,
  Globe,
  Info,
  FileText as PassportIcon,
  ShieldCheck,
  Stamp,
} from 'lucide-react';
import { motion } from 'motion/react';

interface VisaData {
  destination: string;
  visa_required: 'yes' | 'no' | 'depends';
  visa_type: string;
  processing_time: string;
  validity_info: string;
  stay_duration_info: string;
  documents_required: string[];
  visa_notes: string[];
}

interface VisaInfoCardProps {
  data: VisaData;
}

export function VisaInfoCard({ data }: VisaInfoCardProps) {
  const isRequired = data.visa_required === 'yes';
  const isDepends = data.visa_required === 'depends';

  const statusColor = isRequired
    ? 'bg-amber-100 text-amber-700 border-amber-200'
    : isDepends
      ? 'bg-blue-100 text-blue-700 border-blue-200'
      : 'bg-emerald-100 text-emerald-700 border-emerald-200';

  const statusLabel = isRequired
    ? 'Visa Required'
    : isDepends
      ? 'Conditions Apply'
      : 'Visa Free / On Arrival';

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex w-full max-w-full flex-col overflow-hidden rounded-3xl border border-zinc-200 bg-white shadow-2xl md:max-w-[600px]"
    >
      {/* Header */}
      <div className="border-b border-zinc-100 p-8">
        <div className="flex flex-col justify-between gap-6 md:flex-row md:items-center">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-[10px] font-extrabold tracking-[0.2em] text-zinc-400 uppercase">
              <ShieldCheck className="h-4 w-4 text-blue-600" />
              Vetting & Documents
            </div>
            <h2 className="text-3xl font-black tracking-tighter text-zinc-900">Visa Information</h2>
            <div className="flex items-center gap-2 text-sm font-bold text-zinc-500">
              <Globe size={14} className="text-zinc-300" />
              For Indian Citizens visiting {data.destination}
            </div>
          </div>

          <div
            className={`rounded-2xl border px-4 py-2 ${statusColor} flex h-fit items-center gap-2 text-xs font-black tracking-widest whitespace-nowrap uppercase shadow-sm`}
          >
            {isRequired ? <FileText size={14} /> : <CheckCircle2 size={14} />}
            {statusLabel}
          </div>
        </div>
      </div>

      <div className="space-y-8 p-8">
        {/* Key Info Grid */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="space-y-4 rounded-2xl border border-zinc-100 bg-zinc-50 p-6">
            <div className="flex items-center gap-2 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
              <Stamp className="h-4 w-4 text-blue-500" />
              Visa Type & Validity
            </div>
            <div className="space-y-4">
              <div>
                <div className="mb-1 text-[10px] font-bold text-zinc-400">OFFICIAL TYPE</div>
                <div className="text-sm font-black text-zinc-800 uppercase">{data.visa_type}</div>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="mb-1 text-[10px] font-bold text-zinc-400">VALIDITY</div>
                  <div className="text-xs font-bold text-zinc-600">{data.validity_info}</div>
                </div>
                <div>
                  <div className="mb-1 text-[10px] font-bold text-zinc-400">MAX STAY</div>
                  <div className="text-xs font-bold text-zinc-600">{data.stay_duration_info}</div>
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4 rounded-2xl border border-zinc-100 bg-zinc-50 p-6">
            <div className="flex items-center gap-2 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
              <Clock className="h-4 w-4 text-amber-500" />
              Processing Time
            </div>
            <div className="space-y-1">
              <div className="text-sm leading-snug font-black text-zinc-800 uppercase">
                {data.processing_time}
              </div>
              <p className="text-[10px] font-medium text-zinc-400 italic">
                *Estimated timeframe based on current trends
              </p>
            </div>
          </div>
        </div>

        {/* Documentation Section */}
        <div className="space-y-4">
          <h3 className="flex items-center gap-2 text-[10px] font-black tracking-widest text-zinc-400 uppercase">
            <PassportIcon className="h-4 w-4 text-zinc-900" />
            Required Documentation
          </h3>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {data.documents_required.map((doc, i) => (
              <div
                key={i}
                className="group flex items-center gap-3 rounded-xl border border-zinc-100 bg-white p-3 shadow-sm transition-all hover:border-blue-200"
              >
                <div className="h-2 w-2 shrink-0 rounded-full border border-zinc-200 bg-zinc-100 transition-colors group-hover:border-blue-600 group-hover:bg-blue-500" />
                <span className="truncate text-xs font-bold text-zinc-600">{doc}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Notes / Alerts */}
        <div className="space-y-4 rounded-2xl bg-zinc-900 p-6 text-white">
          <div className="flex items-center gap-2 text-[10px] font-black tracking-widest text-white/40 uppercase">
            <AlertCircle className="h-4 w-4 text-amber-400" />
            Critical Travel Notes
          </div>
          <ul className="space-y-2">
            {data.visa_notes.map((note, i) => (
              <li key={i} className="flex gap-3 text-xs leading-relaxed font-medium text-white/80">
                <ChevronRight size={14} className="mt-0.5 shrink-0 text-amber-400" />
                {note}
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Warning Footer */}
      <div className="flex items-center justify-between bg-zinc-50 px-8 py-4 text-[8px] font-black tracking-widest text-zinc-400 uppercase italic">
        <div className="flex items-center gap-2">
          <Info size={10} className="text-blue-500" />
          Always check official consulate portals for real-time updates
        </div>
        <span>Consular Intelligence</span>
      </div>
    </motion.div>
  );
}
