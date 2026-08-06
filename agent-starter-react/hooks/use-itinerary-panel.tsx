'use client';

import React, { createContext, useContext, useState, useCallback, useEffect, useRef } from 'react';

interface ItineraryData {
  [key: string]: any;
}

interface ItineraryEntry {
  id: string;
  data: ItineraryData;
  timestamp: number;
}

interface ItineraryPanelContextType {
  /** Whether the side panel is open */
  isOpen: boolean;
  /** The currently displayed itinerary */
  activeItinerary: ItineraryEntry | null;
  /** All itineraries received so far */
  allItineraries: ItineraryEntry[];
  /** Open the panel with a specific itinerary */
  openItinerary: (entry: ItineraryEntry) => void;
  /** Close the panel */
  closePanel: () => void;
  /** Push a new itinerary (auto-opens it) */
  pushItinerary: (id: string, data: ItineraryData) => void;
}

const ItineraryPanelContext = createContext<ItineraryPanelContextType | null>(null);

export function useItineraryPanel() {
  const ctx = useContext(ItineraryPanelContext);
  if (!ctx) {
    throw new Error('useItineraryPanel must be used within an ItineraryPanelProvider');
  }
  return ctx;
}

export function ItineraryPanelProvider({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeItinerary, setActiveItinerary] = useState<ItineraryEntry | null>(null);
  const [allItineraries, setAllItineraries] = useState<ItineraryEntry[]>([]);
  const knownIdsRef = useRef<Set<string>>(new Set());

  const openItinerary = useCallback((entry: ItineraryEntry) => {
    setActiveItinerary(entry);
    setIsOpen(true);
  }, []);

  const closePanel = useCallback(() => {
    setIsOpen(false);
  }, []);

  const pushItinerary = useCallback(
    (id: string, data: ItineraryData) => {
      const entry: ItineraryEntry = { id, data, timestamp: Date.now() };

      if (!knownIdsRef.current.has(id)) {
        knownIdsRef.current.add(id);
        setAllItineraries((prev) => [...prev, entry]);
      }

      // Always show as active and open when pushed (arrival or manual trigger)
      setActiveItinerary(entry);
      setIsOpen(true);
    },
    []
  );

  return (
    <ItineraryPanelContext.Provider
      value={{ isOpen, activeItinerary, allItineraries, openItinerary, closePanel, pushItinerary }}
    >
      {children}
    </ItineraryPanelContext.Provider>
  );
}
