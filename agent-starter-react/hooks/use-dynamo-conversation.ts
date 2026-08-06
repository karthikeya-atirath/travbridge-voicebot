import { useEffect, useRef, useState } from 'react';

export interface CardData {
  id: string; // use message_id or tool_call id
  type: string; // e.g. 'get_travel_package'
  status: 'loading' | 'success' | 'error';
  data?: any;
  arguments?: any;
  timestamp: number;
}

export function useDynamoConversation(roomName?: string) {
  const [cards, setCards] = useState<CardData[]>([]);
  const processedCallsRef = useRef<Set<string>>(new Set());
  // Track the latest itinerary loading card ID so we can merge it with the output
  const itineraryLoadingIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!roomName) return;

    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/conversation?conversationId=${roomName}`);
        if (!res.ok) return;
        const data = await res.json();

        // conversation is usually an array of message objects
        const conversation = data.conversation || [];

        conversation.forEach((msg: any) => {
          const createdAtString = msg.created_at ? String(msg.created_at) : '';

          // Check if the message contains tool_calls
          if (msg.role === 'assistant' && msg.tool_calls && Array.isArray(msg.tool_calls)) {
            msg.tool_calls.forEach((toolCall: any) => {
              const rawId = toolCall.id || 'unknown';
              const uniqueCallId = `${createdAtString}_${rawId}`;
              if (toolCall.type === 'function' && !processedCallsRef.current.has(uniqueCallId)) {
                processedCallsRef.current.add(uniqueCallId);
                handleNewFunctionCall(toolCall.function, uniqueCallId, msg.created_at);
              }
            });
          }

          // Fallback for flat structure where role is 'function_call'
          if (msg.role === 'function_call') {
            const rawId = msg.id || msg.message_id || msg.name || 'unknown';
            const uniqueCallId = `${createdAtString}_${rawId}`;
            if (!processedCallsRef.current.has(uniqueCallId)) {
              processedCallsRef.current.add(uniqueCallId);
              handleNewFunctionCall(msg, uniqueCallId, msg.created_at);
            }
          }

          // Handle function_call_output for static/provided data
          if (
            msg.role === 'function_call_output' &&
            (msg.name === 'get_destination_info' ||
              msg.name === 'get_destination_food' ||
              msg.name === 'get_destination_weather' ||
              msg.name === 'get_destination_sightseeing' ||
              msg.name === 'get_destination_activities' ||
              msg.name === 'get_destination_visa_info' ||
              msg.name === 'recommend_destinations' ||
              msg.name === 'get_destination_hotels' ||
              msg.name === 'get_destination_flights' ||
              msg.name === 'create_custom_itinerary' ||
              msg.name === 'update_custom_itinerary')
          ) {
            const isItinerary = msg.name === 'create_custom_itinerary' || msg.name === 'update_custom_itinerary';
            const rawId = msg.id || msg.message_id || msg.name || 'unknown';
            const uniqueOutputId = `output_${createdAtString}_${rawId}`;

            if (!processedCallsRef.current.has(uniqueOutputId)) {
              processedCallsRef.current.add(uniqueOutputId);
              let data: any = {};
              try {
                data = typeof msg.content === 'string' ? JSON.parse(msg.content) : msg.content;
              } catch (e) {}

              const timestamp =
                msg.created_at && !isNaN(new Date(msg.created_at).getTime())
                  ? new Date(msg.created_at).getTime()
                  : Date.now();

              const extractedData =
                data.info ||
                data.food ||
                data.weather ||
                data.sightseeing ||
                data.activities ||
                data.visa ||
                data.recommendations ||
                data.hotels ||
                (data.flight_results ? data : null) ||
                data.itinerary_result ||
                data;

              if (isItinerary && itineraryLoadingIdRef.current) {
                // Merge: replace loading card with success card, keep original timestamp
                const loadingId = itineraryLoadingIdRef.current;
                setCards((prev) => {
                  const existingIdx = prev.findIndex(c => c.id === loadingId);
                  if (existingIdx >= 0) {
                    const newCards = [...prev];
                    newCards[existingIdx] = {
                      ...newCards[existingIdx],
                      type: msg.name,
                      status: 'success',
                      data: extractedData,
                      // Keep original timestamp so card stays in its original position
                    };
                    return newCards;
                  }
                  // Fallback: add as new card
                  return [...prev, {
                    id: `itinerary_${timestamp}`,
                    type: msg.name,
                    status: 'success',
                    data: extractedData,
                    timestamp,
                  }];
                });
                itineraryLoadingIdRef.current = null;
              } else {
                setCards((prev) => [...prev, {
                  id: uniqueOutputId,
                  type: msg.name,
                  status: 'success',
                  data: extractedData,
                  timestamp,
                }]);
              }
            }
          }
        });
      } catch (err) {
        console.error('Failed to fetch conversation', err);
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [roomName]);

  const handleNewFunctionCall = async (call: any, callId: string, createdAt?: number | string) => {
    // call.name is the function name, call.arguments is the stringified args
    const rawArgs = call.arguments || '{}';
    let args: any = {};
    try {
      args = typeof rawArgs === 'string' ? JSON.parse(rawArgs) : rawArgs;
    } catch (e) {}

    // Use the message's created_at or fallback to current time
    const timestamp =
      createdAt && !isNaN(new Date(createdAt).getTime())
        ? new Date(createdAt).getTime()
        : Date.now();

    const isItinerary = call.name === 'create_custom_itinerary' || call.name === 'update_custom_itinerary';

    if (
      call.name === 'get_destination_info' ||
      call.name === 'get_destination_food' ||
      call.name === 'get_destination_weather' ||
      call.name === 'get_destination_sightseeing' ||
      call.name === 'get_destination_activities' ||
      call.name === 'get_destination_visa_info' ||
      call.name === 'recommend_destinations' ||
      call.name === 'get_destination_hotels' ||
      call.name === 'get_destination_flights'
    ) {
      return;
    }

    if (isItinerary) {
      // For itinerary tools, create a loading card and track its ID
      const loadingCardId = `itinerary_loading_${timestamp}_${call.name}`;
      itineraryLoadingIdRef.current = loadingCardId;

      const newCard: CardData = {
        id: loadingCardId,
        type: call.name,
        status: 'loading',
        arguments: args,
        timestamp,
      };

      // Remove any previous itinerary loading cards before adding new one
      setCards((prev) => [
        ...prev.filter(c => !(c.type === call.name && c.status === 'loading')),
        newCard,
      ]);
      return;
    }

    const newCard: CardData = {
      id: callId,
      type: call.name || call.type,
      status: 'loading',
      arguments: args,
      timestamp,
    };

    setCards((prev) => [...prev, newCard]);

    let url = '';
    let payload_or_options: RequestInit = {};

    switch (call.name) {
      case 'get_travel_package':
        url = 'https://travbridge.atirath.com/v1/livepackages';
        payload_or_options = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            search_term: args.destination || args.search_term || '',
            number_of_people: args.number_of_people || 2,
            days: args.days || 5,
            budget: args.budget || 50000,
            departureCity: args.hub || args.departureCity || args.departure_city || 'Delhi',
            monthOfTravel: args.month_of_travel || args.monthOfTravel || 'April',
            pkgSubtypeName: args.package_type || args.pkgSubtypeName || 'GIT,FIT',
            fareCalendar: false,
          }),
        };
        break;
      case 'get_fare_calendar':
        url = 'https://travbridge.atirath.com/v1/search_by_package_id';
        payload_or_options = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            packageId: args.package_id || args.packageId,
            departureCity: args.departure_city || args.departureCity || 'Delhi',
            fareCalendar: true,
          }),
        };
        break;
      case 'get_all_bogo_packages':
        url = 'https://travbridge.atirath.com/v1/get_all_BOGO_packages';
        payload_or_options = { method: 'GET' };
        break;
      case 'search_packages_by_name':
        url = 'https://travbridge.atirath.com/v1/search_by_package_name';
        payload_or_options = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            packageName: args.packageName || args.package_name || args.search_term || '',
          }),
        };
        break;
      case 'get_package_pricing':
        url = 'https://travbridge.atirath.com/mcp/tcil/api/get_package_pricing';
        payload_or_options = {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            pkg_id: args.pkg_id || args.package_id || args.packageId || '',
            departure_date: args.departure_date || '09-04-2026',
            hub_city: args.hub_city || args.departure_city || args.departureCity || 'Delhi',
            rooms: args.rooms || [
              {
                roomNo: 1,
                noAdult: 2,
                noCwb: 0,
                noCnbS: 0,
                inf: 0,
                pax: 2,
              },
            ],
            user_mobile_no: args.user_mobile_no || '9999999999',
            user_email_id: args.user_email_id || 'test@test.com',
            is_flight_enabled: args.is_flight_enabled ?? true,
            safe_room_fallback_calculation_pricing:
              args.safe_room_fallback_calculation_pricing ?? false,
            pkg_class_id: args.pkg_class_id != null ? String(args.pkg_class_id) : '',
          }),
        };
        break;
      default:
        // Ignore known non-API tools or errors
        setCards((prev) => prev.filter((c) => c.id !== callId));
        return;
    }

    try {
      const resp = await fetch(url, payload_or_options);
      // the response could be direct JSON or wrapped depending on the endpoint
      const data = await resp.json();
      setCards((prev) =>
        prev.map((c) => (c.id === callId ? { ...c, status: 'success', data } : c))
      );
    } catch (e) {
      console.error(`Failed API call for ${call.name}`, e);
      setCards((prev) => prev.map((c) => (c.id === callId ? { ...c, status: 'error' } : c)));
    }
  };

  return { cards };
}
