import React, { forwardRef } from 'react';
import { Globe2, Mic } from 'lucide-react';
import { motion } from 'motion/react';
import { Button } from '@/components/ui/button';

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => void;
}

export const WelcomeView = forwardRef<
  HTMLDivElement,
  React.ComponentProps<'div'> & WelcomeViewProps
>(({ startButtonText, onStartCall, className, ...props }, ref) => {
  return (
    <div
      ref={ref}
      {...props}
      className={`fixed inset-0 flex flex-col items-center justify-center overflow-hidden bg-black ${className || ''}`}
    >
      {/* Cinematic Background */}
      <div className="absolute inset-0 z-0">
        <motion.img
          initial={{ scale: 1.1 }}
          animate={{ scale: 1 }}
          transition={{ duration: 10, ease: 'easeOut' }}
          src="/luxury_travel_bg_1773075046830.png"
          className="h-full w-full object-cover opacity-80"
          alt="Luxury Travel Resort"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-black/60 via-transparent to-black/80" />
      </div>

      <section className="relative z-20 flex w-full max-w-5xl flex-col items-center px-6">
        <motion.div
          initial={{ y: 40, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
          className="flex flex-col items-center gap-16"
        >
          <div className="space-y-8 text-center">
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ delay: 0.2 }}
              className="mx-auto flex w-fit items-center gap-3 rounded-full border border-white/20 bg-white/5 px-6 py-2 backdrop-blur-md"
            >
              <span className="h-2 w-2 animate-pulse rounded-full bg-blue-400 shadow-[0_0_8px_#60a5fa]" />
              <span className="text-xs font-black tracking-[0.2em] text-white/80 uppercase">
                Thomas Cook India · AI Concierge
              </span>
            </motion.div>

            <motion.h1
              className="text-6xl font-black tracking-tight text-balance text-white sm:text-7xl md:text-9xl"
              initial={{ y: 40, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ delay: 0.4 }}
            >
              Your Journey <br />
              <span className="bg-gradient-to-r from-blue-100 to-blue-400 bg-clip-text text-transparent">
                Starts Here
              </span>
            </motion.h1>

            <motion.p
              className="mx-auto max-w-2xl text-lg leading-relaxed font-medium text-blue-100/60 sm:text-xl md:text-2xl"
              initial={{ y: 20, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              transition={{ delay: 0.6 }}
            >
              Speak with our AI specialist — personalized, real-time assistance.
            </motion.p>
          </div>

          <motion.div
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.8 }}
            className="flex flex-col items-center gap-12"
          >
            <Button
              size="lg"
              onClick={onStartCall}
              className="group relative h-24 min-w-[320px] overflow-hidden rounded-[2rem] border border-white/20 bg-white/5 p-0 text-3xl font-black text-white shadow-2xl transition-all hover:scale-105 hover:bg-white hover:text-black active:scale-95"
            >
              <div className="absolute inset-0 flex items-center justify-center gap-4 px-12 transition-transform group-hover:bg-white group-hover:text-black">
                <Mic className="h-8 w-8" />
                <span>{startButtonText}</span>
              </div>
            </Button>

            <div className="flex flex-col items-center gap-4">
              <p className="flex items-center gap-3 text-xs font-black tracking-[0.3em] text-white/40 uppercase">
                <Globe2 className="h-4 w-4" />
                Multilingual Support Available
              </p>
              <div className="flex gap-2 opacity-50">
                <span className="h-0.5 w-12 rounded-full bg-white/20" />
                <span className="h-0.5 w-12 rounded-full bg-blue-500" />
                <span className="h-0.5 w-12 rounded-full bg-white/20" />
              </div>
            </div>
          </motion.div>
        </motion.div>
      </section>

      <div className="pointer-events-none absolute inset-0 z-10 bg-[radial-gradient(circle_at_50%_120%,rgba(37,99,235,0.1),transparent)]" />
    </div>
  );
});

WelcomeView.displayName = 'WelcomeView';
