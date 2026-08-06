import Link from 'next/link';
import { Plane, CreditCard, Globe2 } from 'lucide-react';

export const metadata = {
  title: 'Thomas Cook India · AI Concierge',
  description: 'Speak with a Thomas Cook AI specialist for Travel or Forex',
};

export default function LandingPage() {
  return (
    <main className="fixed inset-0 flex flex-col items-center justify-center overflow-hidden bg-black">
      {/* Background */}
      <div className="absolute inset-0 z-0">
        <img
          src="/luxury_travel_bg_1773075046830.png"
          className="h-full w-full object-cover opacity-70"
          alt="Thomas Cook"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-black/70 via-black/30 to-black/90" />
      </div>

      <section className="relative z-20 flex w-full max-w-4xl flex-col items-center gap-12 px-6 text-center">
        {/* Badge */}
        <div className="flex items-center gap-3 rounded-full border border-white/20 bg-white/5 px-6 py-2 backdrop-blur-md">
          <span className="h-2 w-2 animate-pulse rounded-full bg-blue-400 shadow-[0_0_8px_#60a5fa]" />
          <span className="text-xs font-black tracking-[0.2em] text-white/80 uppercase">
            Thomas Cook India · AI Concierge
          </span>
        </div>

        {/* Heading */}
        <div className="space-y-5">
          <h1 className="text-5xl font-black tracking-tight text-white sm:text-7xl">
            Your Journey{' '}
            <span className="bg-gradient-to-r from-blue-100 to-blue-400 bg-clip-text text-transparent">
              Starts Here
            </span>
          </h1>
          <p className="mx-auto max-w-lg text-lg text-white/50 font-medium">
            Choose a specialist to connect with our AI-powered voice assistant.
          </p>
        </div>

        {/* Navigation Cards */}
        <div className="flex w-full max-w-2xl flex-col gap-5 sm:flex-row sm:gap-6">
          {/* Travel */}
          <Link
            href="/travel"
            className="group relative flex-1 overflow-hidden rounded-3xl border border-white/10 bg-white/5 p-8 text-left backdrop-blur-md transition-all duration-300 hover:border-blue-400/40 hover:bg-white/10 hover:shadow-[0_0_40px_rgba(59,130,246,0.25)] hover:-translate-y-1"
          >
            <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-blue-500/0 to-blue-500/0 transition-all duration-300 group-hover:from-blue-500/10 group-hover:to-cyan-500/10" />
            <div className="relative z-10 flex flex-col gap-5">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/15 bg-white/8 text-white transition-all group-hover:border-blue-400/40 group-hover:bg-blue-500/20">
                <Plane className="h-7 w-7" />
              </div>
              <div>
                <h2 className="text-3xl font-black text-white">Travel</h2>
                <p className="mt-1 text-sm text-white/50">Packages · Itineraries · Fare Calendar</p>
                <p className="mt-1 text-xs text-white/35">Tacy · Travel Expert</p>
              </div>
              <div className="flex items-center gap-2 text-sm font-bold text-white/40 transition-all group-hover:text-blue-300">
                <span>Connect with Tacy</span>
                <span className="transition-transform group-hover:translate-x-1">→</span>
              </div>
            </div>
          </Link>

          {/* Forex */}
          <Link
            href="/forex"
            className="group relative flex-1 overflow-hidden rounded-3xl border border-white/10 bg-white/5 p-8 text-left backdrop-blur-md transition-all duration-300 hover:border-emerald-400/40 hover:bg-white/10 hover:shadow-[0_0_40px_rgba(16,185,129,0.25)] hover:-translate-y-1"
          >
            <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-emerald-500/0 to-teal-500/0 transition-all duration-300 group-hover:from-emerald-500/10 group-hover:to-teal-500/10" />
            <div className="relative z-10 flex flex-col gap-5">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-white/15 bg-white/8 text-white transition-all group-hover:border-emerald-400/40 group-hover:bg-emerald-500/20">
                <CreditCard className="h-7 w-7" />
              </div>
              <div>
                <h2 className="text-3xl font-black text-white">Forex</h2>
                <p className="mt-1 text-sm text-white/50">Buy · Sell · Reload · Remittance</p>
                <p className="mt-1 text-xs text-white/35">Priya · Forex Specialist</p>
              </div>
              <div className="flex items-center gap-2 text-sm font-bold text-white/40 transition-all group-hover:text-emerald-300">
                <span>Connect with Priya</span>
                <span className="transition-transform group-hover:translate-x-1">→</span>
              </div>
            </div>
          </Link>
        </div>

        {/* Footer */}
        <p className="flex items-center gap-3 text-xs font-black tracking-[0.3em] text-white/30 uppercase">
          <Globe2 className="h-4 w-4" />
          Multilingual · English &amp; Hindi
        </p>
      </section>
    </main>
  );
}
