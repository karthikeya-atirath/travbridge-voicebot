'use client';

import React, { useState, useEffect } from 'react';
import { motion } from 'motion/react';
import {
  Globe,
  Plane,
  Hotel,
  Map,
  CalendarCheck,
  Loader2,
  Check,
} from 'lucide-react';
import { cn } from '@/lib/shadcn/utils';

interface Step {
  id: number;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
}

const CREATE_STEPS: Step[] = [
  { id: 1, label: 'Analyzing your travel preferences...', icon: Globe },
  { id: 2, label: 'Searching for optimal flight routes...', icon: Plane },
  { id: 3, label: 'Curating premium accommodations...', icon: Hotel },
  { id: 4, label: 'Mapping out local experiences...', icon: Map },
  { id: 5, label: 'Finalizing your custom day-wise plan...', icon: CalendarCheck },
];

const UPDATE_STEPS: Step[] = [
  { id: 1, label: 'Reviewing your changes...', icon: Globe },
  { id: 2, label: 'Re-optimizing flight routes...', icon: Plane },
  { id: 3, label: 'Updating accommodations...', icon: Hotel },
  { id: 4, label: 'Adjusting local experiences...', icon: Map },
  { id: 5, label: 'Rebuilding your day-wise plan...', icon: CalendarCheck },
];

interface ItineraryBuildingCardProps {
  isUpdate?: boolean;
}

export function ItineraryBuildingCard({ isUpdate = false }: ItineraryBuildingCardProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const steps = isUpdate ? UPDATE_STEPS : CREATE_STEPS;

  useEffect(() => {
    const interval = setInterval(() => {
      setCurrentStep((prev) => (prev < steps.length - 1 ? prev + 1 : prev));
    }, 2500);
    return () => clearInterval(interval);
  }, [steps.length]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="relative w-full max-w-[440px] overflow-hidden rounded-3xl border border-indigo-200 bg-white/40 p-1 shadow-2xl backdrop-blur-2xl dark:border-indigo-500/20 dark:bg-black/40"
    >
      <div className="relative overflow-hidden rounded-[22px] bg-linear-to-br from-indigo-50/50 to-white/50 p-6 dark:from-indigo-950/20 dark:to-zinc-950/20">
        {/* Background glow */}
        <div className="absolute inset-0 -z-10 bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.1),transparent_70%)]" />

        <div className="flex items-start justify-between mb-6">
           <div className="space-y-1">
             <h3 className="text-lg font-black tracking-tight text-zinc-900 dark:text-white">
               {isUpdate ? 'Updating Your Itinerary' : 'Building Your Itinerary'}
             </h3>
             <p className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
               {isUpdate
                 ? 'Applying your changes — almost there!'
                 : 'Hang tight! We\'re crafting something special for you.'}
             </p>
           </div>
           <Loader2 className="h-5 w-5 animate-spin text-indigo-500" />
        </div>

        {/* Steps List */}
        <div className="space-y-3">
          {steps.map((step, idx) => {
            const StepIcon = step.icon;
            const isActive = idx === currentStep;
            const isCompleted = idx < currentStep;

            return (
              <motion.div
                key={step.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{
                  opacity: isActive || isCompleted ? 1 : 0.4,
                  x: 0,
                  scale: isActive ? 1.02 : 1
                }}
                className={cn(
                  "flex items-center gap-3 transition-all duration-500",
                  isActive ? "text-indigo-600 dark:text-indigo-400" : "text-zinc-400 dark:text-zinc-600"
                )}
              >
                <div className={cn(
                   "flex h-8 w-8 shrink-0 items-center justify-center rounded-xl transition-all duration-500",
                   isActive ? "bg-indigo-600 text-white shadow-lg shadow-indigo-600/20" :
                   isCompleted ? "bg-emerald-500 text-white" : "bg-zinc-100 dark:bg-white/5 opacity-50"
                )}>
                  {isCompleted ? (
                    <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }}>
                       <Check size={14} />
                    </motion.div>
                  ) : (
                    <StepIcon size={14} className={isActive ? "animate-pulse" : ""} />
                  )}
                </div>
                <div className="flex flex-col min-w-0">
                  <span className={cn(
                    "text-[12px] font-bold tracking-tight",
                    isActive && "text-zinc-900 dark:text-white",
                    isCompleted && "text-emerald-600 dark:text-emerald-400"
                  )}>
                    {step.label}
                  </span>
                  {isActive && (
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: "100%" }}
                      className="h-0.5 bg-indigo-500/30 mt-1 rounded-full overflow-hidden"
                    >
                      <motion.div
                        animate={{ x: ["-100%", "100%"] }}
                        transition={{ repeat: Infinity, duration: 1.5, ease: "linear" }}
                        className="h-full w-1/3 bg-indigo-600 rounded-full"
                      />
                    </motion.div>
                  )}
                </div>
              </motion.div>
            );
          })}
        </div>
      </div>
    </motion.div>
  );
}
