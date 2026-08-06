/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useState } from 'react';
import { CheckCircle2, Plane, ReceiptText, Users } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useChat } from '@livekit/components-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface PricingCardProps {
  data: any;
}

export function PricingCard({ data }: PricingCardProps) {
  const { send } = useChat();
  const [open, setOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [formData, setFormData] = useState({ name: '', phone: '', email: '' });

  const details =
    data?.data?.pricing_details ||
    data?.pricing_details?.data ||
    data?.pricing_details ||
    data?.data?.data?.data ||
    data?.data?.data ||
    data?.data ||
    data?.body ||
    data;

  const isArrayData = Array.isArray(details);
  const arrayPrice = isArrayData && details.length > 0 ? details[0].price : null;

  if (
    !details ||
    (!details.totalPrice && !details.netPrice && !details.grossPrice && !arrayPrice)
  ) {
    return (
      <div className="rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-600">
        Could not retrieve pricing details.
      </div>
    );
  }

  const {
    totalTax = 0,
    totalDiscount = 0,
    netPrice = 0,
    currencySummary = '',
    rooms = [],
  } = isArrayData ? {} : details;
  const flightsInc = data?.is_flight_enabled === true;

  let totalAdults = 0;
  let totalChildren = 0;
  const totalRooms = rooms?.length || 1;

  if (rooms && rooms.length > 0) {
    rooms.forEach((r: any) => {
      totalAdults += r.noAdult || 0;
      totalChildren += (r.noCwb || 0) + (r.noCnbS || 0) + (r.noCnbJ || 0) + (r.inf || 0);
    });
  } else {
    totalAdults = 2;
  }

  const roomText = `${totalAdults} Adult${totalAdults > 1 ? 's' : ''}${totalChildren > 0 ? `, ${totalChildren} Child${totalChildren > 1 ? 'ren' : ''}` : ''} • ${totalRooms} Room${totalRooms > 1 ? 's' : ''}`;

  const apiNetTotal = isArrayData ? arrayPrice : details.totalPrice || netPrice + totalTax;

  const formatINR = (val: number) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 0,
    }).format(val);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.name && formData.phone) {
      setSubmitted(true);
      // Send a message to the agent context
      if (send) {
        send(
          `I have submitted a quote request. Name: ${formData.name}, Phone: ${formData.phone}, Email: ${formData.email}. Please proceed with the booking.`
        );
      }
      setTimeout(() => setOpen(false), 2000);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="my-4 flex w-full max-w-full flex-col gap-4 overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-sm backdrop-blur-md md:max-w-[600px] sm:flex-row dark:border-neutral-800 dark:bg-zinc-900"
      >
        {/* Pricing Summary Side */}
        <div className="flex w-full flex-col bg-[#0a4ca0] p-6 text-white sm:w-1/2 sm:rounded-r-[2rem]">
          <div className="mb-4 flex items-center justify-between border-b border-blue-400/30 pb-4">
            <h3 className="flex items-center gap-2 text-xl font-bold">
              <ReceiptText className="h-6 w-6" /> Pricing Summary
            </h3>
          </div>

          <div className="mb-6 flex flex-col gap-1 text-sm opacity-90">
            {rooms && rooms.length > 0 ? (
              rooms.map((room: any, idx: number) => (
                <p key={idx} className="font-medium">
                  Room {room.roomNo}: {room.noAdult} Adult{room.noAdult > 1 ? 's' : ''}
                  {room.noCwb + room.noCnbJ + room.noCnbS + room.inf > 0
                    ? `, ${room.noCwb + room.noCnbJ + room.noCnbS + room.inf} Child`
                    : ''}
                </p>
              ))
            ) : (
              <p className="font-medium">For {roomText}</p>
            )}
          </div>

          <div className="mt-auto flex flex-col gap-2 pt-4">
            <span className="text-sm font-medium tracking-widest text-blue-200 uppercase">
              Base Price
            </span>
            <span className="text-3xl font-black">{formatINR(apiNetTotal)}</span>
            <span className="text-xs text-blue-200">
              {currencySummary || 'Inclusive of taxes & fees'}
            </span>
          </div>
        </div>

        {/* Flight & Actions Side */}
        <div className="flex w-full flex-col justify-between bg-zinc-50 p-6 sm:w-1/2 dark:bg-zinc-900/50">
          <div className="flex flex-col gap-4">
            {details.flights && details.flights.length > 0 ? (
              <div className="flex flex-col gap-3">
                <span className="text-xs font-bold tracking-widest text-neutral-400 uppercase">
                  Included Flights
                </span>
                {details.flights.map((f: any, i: number) => (
                  <div
                    key={i}
                    className="relative overflow-hidden rounded-xl bg-gradient-to-br from-indigo-500 to-[#1e4ed8] text-white shadow-md"
                  >
                    <div className="flex border-b border-dashed border-white/20 px-4 py-3">
                      <div className="flex-1">
                        <span className="text-[10px] text-indigo-200 uppercase">Flight</span>
                        <div className="truncate text-sm font-bold">{f.flightNo}</div>
                      </div>
                      <div className="text-right">
                        <span className="text-[10px] text-indigo-200 uppercase">Airline</span>
                        <div className="truncate text-sm font-bold">{f.airline}</div>
                      </div>
                    </div>
                    {/* Boarding Pass Cutouts */}
                    <div className="absolute top-[48px] -left-3 h-5 w-5 rounded-full bg-zinc-50 dark:bg-zinc-900" />
                    <div className="absolute top-[48px] -right-3 h-5 w-5 rounded-full bg-zinc-50 dark:bg-zinc-900" />

                    <div className="flex items-center justify-between p-4">
                      <div className="w-1/3 text-center">
                        <span className="text-2xl font-black">
                          {f.departureCity.substring(0, 3).toUpperCase()}
                        </span>
                        <div className="truncate text-[9px] text-indigo-200">
                          {f.departureCity.split(',')[0]}
                        </div>
                      </div>
                      <div className="relative flex flex-1 flex-col items-center justify-center border-b-2 border-dashed border-white/30 px-2 pb-2">
                        <Plane className="absolute -bottom-[9px] h-4 w-4 text-white" />
                        <span className="mt-1 text-[10px] font-bold text-indigo-100">
                          {f.duration}
                        </span>
                      </div>
                      <div className="w-1/3 text-center">
                        <span className="text-2xl font-black">
                          {f.arrivalCity.substring(0, 3).toUpperCase()}
                        </span>
                        <div className="truncate text-[9px] text-indigo-200">
                          {f.arrivalCity.split(',')[0]}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : flightsInc ? (
              <div className="flex flex-col gap-2 rounded-xl border border-blue-200/50 bg-blue-100/50 p-4 dark:border-blue-800/50 dark:bg-blue-900/20">
                <div className="flex items-center gap-3">
                  <div className="rounded-full bg-blue-500 p-2 text-white shadow-sm">
                    <Plane className="h-5 w-5" />
                  </div>
                  <span className="text-sm font-bold text-blue-900 dark:text-blue-100">
                    Flights Included
                  </span>
                </div>
                <p className="pl-11 text-xs text-blue-700 dark:text-blue-300">
                  Airlines will be assigned shortly.
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-2 rounded-xl border border-orange-200/50 bg-orange-50 p-4 dark:border-orange-800/50 dark:bg-orange-900/20">
                <div className="flex items-center gap-3">
                  <div className="rounded-full bg-orange-400 p-2 text-white shadow-sm">
                    <Users className="h-5 w-5" />
                  </div>
                  <span className="text-sm font-bold text-orange-900 dark:text-orange-100">
                    Land Only Package
                  </span>
                </div>
                <p className="pl-11 text-xs text-orange-700 dark:text-orange-300">
                  Does not include flights.
                </p>
              </div>
            )}
          </div>

          <div className="mt-6 flex flex-col gap-3">
            <div className="flex items-center justify-between text-xs text-neutral-500">
              <span>Includes {formatINR(totalTax)} Taxes</span>
              {totalDiscount > 0 && (
                <span className="font-bold text-emerald-500">
                  -{formatINR(totalDiscount)} Discount
                </span>
              )}
            </div>
            <DialogTrigger asChild>
              <Button
                onClick={() => setSubmitted(false)}
                className="w-full rounded-xl bg-[#0a4ca0] py-6 text-lg font-bold shadow-md transition-transform hover:scale-[1.02] hover:bg-[#083b7e]"
              >
                Get Free Quote
              </Button>
            </DialogTrigger>
          </div>
        </div>
      </motion.div>

      <DialogContent className="rounded-2xl border-none bg-white p-6 sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-2xl font-bold">
            <ReceiptText className="text-[#0a4ca0]" /> Get Your Quote
          </DialogTitle>
          <p className="mt-2 text-sm text-neutral-500">
            Fill in your details and an agent will confirm your personalized quote shortly.
          </p>
        </DialogHeader>

        <AnimatePresence mode="wait">
          {submitted ? (
            <motion.div
              key="success"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex flex-col items-center justify-center gap-4 py-12 text-center"
            >
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100">
                <CheckCircle2 className="h-8 w-8 text-emerald-500" />
              </div>
              <h3 className="text-xl font-bold text-neutral-800">Quote Requested!</h3>
              <p className="text-sm text-neutral-500">
                We have sent your request to the chat context. An agent will assist you soon.
              </p>
            </motion.div>
          ) : (
            <motion.form
              key="form"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onSubmit={handleSubmit}
              className="mt-4 flex flex-col gap-6"
            >
              <div className="flex flex-col gap-2">
                <Label htmlFor="name" className="font-bold text-neutral-700">
                  Full Name *
                </Label>
                <Input
                  id="name"
                  placeholder="John Doe"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  required
                  className="rounded-xl border-neutral-200 bg-neutral-50 focus-visible:ring-[#0a4ca0]"
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="phone" className="font-bold text-neutral-700">
                  Mobile Number *
                </Label>
                <Input
                  id="phone"
                  type="tel"
                  placeholder="+91 98765 43210"
                  value={formData.phone}
                  onChange={(e) => setFormData({ ...formData, phone: e.target.value })}
                  required
                  className="rounded-xl border-neutral-200 bg-neutral-50 focus-visible:ring-[#0a4ca0]"
                />
              </div>
              <div className="flex flex-col gap-2">
                <Label htmlFor="email" className="font-bold text-neutral-700">
                  Email Address
                </Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="john@example.com"
                  value={formData.email}
                  onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  className="rounded-xl border-neutral-200 bg-neutral-50 focus-visible:ring-[#0a4ca0]"
                />
              </div>

              <Button
                type="submit"
                className="mt-2 w-full rounded-xl bg-[#0a4ca0] py-6 text-lg font-bold hover:bg-[#083b7e]"
              >
                Request Quote
              </Button>
            </motion.form>
          )}
        </AnimatePresence>
      </DialogContent>
    </Dialog>
  );
}
