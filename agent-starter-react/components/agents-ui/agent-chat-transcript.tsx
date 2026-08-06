'use client';

import { type ComponentProps } from 'react';
import { AnimatePresence } from 'motion/react';
import {
  type AgentState,
  type ReceivedMessage,
  useSessionContext,
} from '@livekit/components-react';
import { AgentChatIndicator } from '@/components/agents-ui/agent-chat-indicator';
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from '@/components/ai-elements/conversation';
import { Message, MessageContent, MessageResponse } from '@/components/ai-elements/message';
import { cn } from '@/lib/shadcn/utils';
import { useItineraryPanel } from '@/hooks/use-itinerary-panel';
import {
  ActivitiesCard,
  CustomItineraryCard,
  DestinationFlightsCard,
  DestinationFoodCard,
  DestinationHotelsCard,
  DestinationInfoCard,
  FareCalendarCard,
  ItineraryBuildingCard,
  PackageList,
  PricingCard,
  RecommendDestinationsCard,
  SightseeingCard,
  VisaInfoCard,
  WeatherCard,
} from '@/components/custom-cards';
import { type CardData, useDynamoConversation } from '@/hooks/use-dynamo-conversation';

/**
 * Props for the AgentChatTranscript component.
 */
export interface AgentChatTranscriptProps extends ComponentProps<'div'> {
  /**
   * The current state of the agent. When 'thinking', displays a loading indicator.
   */
  agentState?: AgentState;
  /**
   * Array of messages to display in the transcript.
   * @defaultValue []
   */
  messages?: ReceivedMessage[];
  /**
   * Additional CSS class names to apply to the conversation container.
   */
  className?: string;
}

/**
 * A chat transcript component that displays a conversation between the user and agent.
 * Shows messages with timestamps and origin indicators, plus a thinking indicator
 * when the agent is processing.
 */
export function AgentChatTranscript({
  agentState,
  messages = [],
  className,
  ...props
}: AgentChatTranscriptProps) {
  const session = useSessionContext();
  const roomName = session?.room?.name;
  const { cards } = useDynamoConversation(roomName);

  // Combine LiveKit messages with DynamoDB cards sequentially by timestamp
  const combinedFeed = [
    ...messages.map((m) => ({ ...m, _type: 'message' })),
    ...cards.map((c) => ({ ...c, _type: 'card' })),
  ].sort((a, b) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const timeA = a._type === 'message' ? (a as ReceivedMessage).timestamp : (a as any).timestamp;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const timeB = b._type === 'message' ? (b as ReceivedMessage).timestamp : (b as any).timestamp;
    return timeA - timeB;
  });

  const { isOpen } = useItineraryPanel();

  return (
    <Conversation className={className} {...props}>
      <ConversationContent>
        {combinedFeed.map((item) => {
          if (item._type === 'message') {
            const { id, timestamp, from, message } = item as ReceivedMessage & { _type: string };
            const locale = navigator?.language ?? 'en-US';
            const messageOrigin = from?.isLocal ? 'user' : 'assistant';
            const time = new Date(timestamp);
            const title = time.toLocaleTimeString(locale, { timeStyle: 'full' });

            return (
              <Message key={`msg-${id}`} title={title} from={messageOrigin}>
                <MessageContent>
                  <div className={cn(
                    "flex w-full flex-col gap-3 transition-all",
                    isOpen ? "max-w-3xl lg:max-w-4xl" : "max-w-[600px]"
                  )}>
                    <MessageResponse>{message}</MessageResponse>
                  </div>
                </MessageContent>
              </Message>
            );
          } else {
            const card = item as CardData & { _type: string };

            if (card.status === 'loading') {
              const isItineraryTool = card.type === 'create_custom_itinerary' || card.type === 'update_custom_itinerary';
              return (
                <Message key={`card-${card.id}`} title="Agent" from="assistant">
                  <MessageContent>
                    {isItineraryTool ? <ItineraryBuildingCard isUpdate={card.type === 'update_custom_itinerary'} /> : <AgentChatIndicator size="sm" />}
                  </MessageContent>
                </Message>
              );
            }

            if (card.status === 'error') {
              return null;
            }

            let cardUI = null;
            switch (card.type) {
              case 'get_travel_package':
              case 'get_all_bogo_packages':
              case 'get_all_BOGO_packages':
              case 'search_packages_by_name':
              case 'search_packages_by_id':
                cardUI = <PackageList data={card.data} />;
                break;
              case 'get_fare_calendar':
                cardUI = <FareCalendarCard data={card.data} />;
                break;
              case 'get_package_pricing':
                cardUI = <PricingCard data={card.data} />;
                break;
              case 'get_destination_info':
                cardUI = <DestinationInfoCard data={card.data} />;
                break;
              case 'get_destination_food':
                cardUI = <DestinationFoodCard data={card.data} />;
                break;
              case 'get_destination_weather':
                cardUI = <WeatherCard data={card.data} />;
                break;
              case 'get_destination_sightseeing':
                cardUI = <SightseeingCard data={card.data} />;
                break;
              case 'get_destination_activities':
                cardUI = <ActivitiesCard data={card.data} />;
                break;
              case 'get_destination_visa_info':
                cardUI = <VisaInfoCard data={card.data} />;
                break;
              case 'recommend_destinations':
                cardUI = <RecommendDestinationsCard data={card.data} />;
                break;
              case 'get_destination_hotels':
                cardUI = <DestinationHotelsCard data={card.data} />;
                break;
              case 'get_destination_flights':
                cardUI = <DestinationFlightsCard data={card.data} />;
                break;
              case 'create_custom_itinerary':
              case 'update_custom_itinerary':
                cardUI = <CustomItineraryCard data={card.data} cardId={card.id} />;
                break;
            }

            if (!cardUI) return null;

              return (
                <Message key={`card-${card.id}`} title="System" from="assistant">
                  <div
                    className={cn(
                      "flex w-full justify-start overflow-visible pb-2 transition-all",
                      isOpen || card.type.toLowerCase().includes('package') ? 'max-w-3xl lg:max-w-4xl' : 'max-w-[600px]'
                    )}
                  >
                    {cardUI}
                  </div>
                </Message>
              );
          }
        })}

        <AnimatePresence>
          {agentState === 'thinking' && <AgentChatIndicator size="sm" />}
        </AnimatePresence>
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
