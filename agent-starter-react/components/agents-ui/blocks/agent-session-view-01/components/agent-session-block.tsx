'use client';

import React, { useEffect, useRef } from 'react';
import { AnimatePresence, type MotionProps, motion } from 'motion/react';
import { useAgent, useSessionContext, useSessionMessages } from '@livekit/components-react';
import { AgentChatTranscript } from '@/components/agents-ui/agent-chat-transcript';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { Shimmer } from '@/components/ai-elements/shimmer';
import { cn } from '@/lib/shadcn/utils';
import { AudioVisualizer } from './audio-visualizer';
import { ItineraryPanelProvider, useItineraryPanel } from '@/hooks/use-itinerary-panel';
import { ItinerarySidePanelDesktop, ItinerarySidePanelMobile } from '@/components/custom-cards/itinerary-side-panel';

const MotionMessage = motion.create(Shimmer);

const BOTTOM_VIEW_MOTION_PROPS: MotionProps = {
  variants: {
    visible: { opacity: 1, translateY: '0%' },
    hidden: { opacity: 0, translateY: '100%' },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: { duration: 0.3, delay: 0.5, ease: 'easeOut' },
};

const CHAT_MOTION_PROPS: MotionProps = {
  variants: {
    hidden: { opacity: 0, transition: { ease: 'easeOut', duration: 0.3 } },
    visible: { opacity: 1, transition: { delay: 0.2, ease: 'easeOut', duration: 0.3 } },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
};

const SHIMMER_MOTION_PROPS: MotionProps = {
  variants: {
    visible: { opacity: 1, transition: { ease: 'easeIn', duration: 0.5, delay: 0.8 } },
    hidden: { opacity: 0, transition: { ease: 'easeIn', duration: 0.5, delay: 0 } },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
};

interface FadeProps {
  top?: boolean;
  bottom?: boolean;
  className?: string;
}

export function Fade({ top = false, bottom = false, className }: FadeProps) {
  return (
    <div
      className={cn(
        'from-background pointer-events-none h-4 bg-linear-to-b to-transparent',
        top && 'bg-linear-to-b',
        bottom && 'bg-linear-to-t',
        className
      )}
    />
  );
}

export interface AgentSessionView_01Props {
  preConnectMessage?: string;
  supportsChatInput?: boolean;
  supportsVideoInput?: boolean;
  supportsScreenShare?: boolean;
  isPreConnectBufferEnabled?: boolean;
  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura';
  audioVisualizerColor?: `#${string}`;
  audioVisualizerColorShift?: number;
  audioVisualizerBarCount?: number;
  audioVisualizerGridRowCount?: number;
  audioVisualizerGridColumnCount?: number;
  audioVisualizerRadialBarCount?: number;
  audioVisualizerRadialRadius?: number;
  audioVisualizerWaveLineWidth?: number;
  className?: string;
}

/**
 * Inner component — uses a plain flex row for the 50/50 desktop split.
 * No absolute/fixed positioning on desktop. Just two flex children side by side.
 */
function AgentSessionView_Inner({
  preConnectMessage = 'Agent is listening, ask it a question',
  supportsVideoInput = true,
  supportsScreenShare = true,
  isPreConnectBufferEnabled = true,
  audioVisualizerType,
  audioVisualizerColor,
  audioVisualizerColorShift,
  audioVisualizerBarCount,
  audioVisualizerGridRowCount,
  audioVisualizerGridColumnCount,
  audioVisualizerRadialBarCount,
  audioVisualizerRadialRadius,
  audioVisualizerWaveLineWidth,
  className,
  ...props
}: React.ComponentProps<'section'> & AgentSessionView_01Props) {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const { state: agentState } = useAgent();
  const [isAutoScrollEnabled, setIsAutoScrollEnabled] = React.useState(true);
  const { isOpen } = useItineraryPanel();

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setIsAutoScrollEnabled(isAtBottom);
  };

  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    camera: supportsVideoInput,
    screenShare: supportsScreenShare,
  };

  useEffect(() => {
    if (scrollAreaRef.current && isAutoScrollEnabled) {
      scrollAreaRef.current.scrollTop = scrollAreaRef.current.scrollHeight;
    }
  }, [messages, isAutoScrollEnabled]);

  return (
    /* ── Master Grid Container (Fixed Inset-0 provided via className) ── */
    <div
      className={cn(
        "z-10 grid h-full w-full overflow-hidden bg-white",
        isOpen ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1",
        className
      )}
      {...props}
    >
      {/* ════════════════════════════════════════════════════════
         LEFT: Chat Column (Column 1)
         ════════════════════════════════════════════════════════ */}
      <div
        className={cn(
          "relative flex h-full min-w-0 flex-col overflow-hidden transition-all duration-500 ease-in-out border-r border-zinc-100 dark:border-zinc-800",
          !isOpen && "w-full"
        )}
      >
        <section
          className={cn('relative flex h-full w-full flex-col overflow-hidden transition-colors duration-500')}
        >
          <Fade
            top
            className="absolute inset-x-4 top-0 z-10 h-10 bg-gradient-to-b from-white to-transparent"
          />

          <div className="relative flex w-full flex-1 flex-col overflow-hidden">
            {isPreConnectBufferEnabled && messages.length === 0 ? null : (
              <div className="absolute top-6 left-1/2 z-30 -translate-x-1/2">
                <motion.div
                  initial={{ y: -20, opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  className="flex items-center justify-center rounded-[28px] border border-white/20 bg-black/10 p-4 backdrop-blur-xl"
                >
                  <AudioVisualizer
                    audioVisualizerType={audioVisualizerType}
                    audioVisualizerColor={audioVisualizerColor}
                    audioVisualizerColorShift={audioVisualizerColorShift}
                    audioVisualizerBarCount={audioVisualizerBarCount}
                    audioVisualizerRadialBarCount={audioVisualizerRadialBarCount}
                    audioVisualizerRadialRadius={audioVisualizerRadialRadius}
                    audioVisualizerGridRowCount={audioVisualizerGridRowCount}
                    audioVisualizerGridColumnCount={audioVisualizerGridColumnCount}
                    audioVisualizerWaveLineWidth={audioVisualizerWaveLineWidth}
                    isChatOpen={true}
                    className="size-12 md:size-16"
                  />
                </motion.div>
              </div>
            )}

            <div
              ref={scrollAreaRef}
              onScroll={handleScroll}
              className="hide-scrollbar flex w-full flex-1 flex-col overflow-x-hidden overflow-y-auto"
            >
              <motion.div {...CHAT_MOTION_PROPS} className="flex w-full flex-1 flex-col">
                <AgentChatTranscript
                  agentState={agentState}
                  messages={messages}
                  className="w-full max-w-none px-4 pt-44 pb-32 md:px-6 [&_.is-user>div]:rounded-[22px] [&_.is-user>div]:bg-zinc-100 [&_.is-user>div]:text-zinc-900"
                />
              </motion.div>
            </div>
          </div>

          <motion.div
            {...BOTTOM_VIEW_MOTION_PROPS}
            className="absolute inset-x-3 bottom-0 z-50 md:inset-x-6"
          >
            {isPreConnectBufferEnabled && (
              <AnimatePresence>
                {messages.length === 0 && (
                  <MotionMessage
                    key="pre-connect-message"
                    duration={2}
                    {...SHIMMER_MOTION_PROPS}
                    className="mx-auto block w-full max-w-2xl pb-4 text-center text-sm font-semibold text-zinc-500"
                  >
                    {preConnectMessage}
                  </MotionMessage>
                )}
              </AnimatePresence>
            )}
            <div className="relative mx-auto flex max-w-2xl justify-center bg-transparent pb-3 md:pb-8">
              <Fade
                bottom
                className="absolute inset-x-0 bottom-0 -z-10 h-32 bg-gradient-to-t from-white via-white to-transparent"
              />
              <AgentControlBar
                variant="livekit"
                controls={controls}
                isConnected={session.isConnected}
                onDisconnect={session.end}
              />
            </div>
          </motion.div>
        </section>
      </div>

      {/* ════════════════════════════════════════════════════════
         RIGHT: Itinerary Column (Desktop)
         ════════════════════════════════════════════════════════ */}
      {isOpen && (
        <div className="hidden h-full min-w-0 flex-col overflow-hidden md:flex">
          <ItinerarySidePanelDesktop />
        </div>
      )}

      {/* ════════════════════════════════════════════════════════
         RIGHT: Itinerary Panel (Mobile — fixed overlay)
         - Full-screen fixed overlay on mobile only.
         ════════════════════════════════════════════════════════ */}
      <div className="md:hidden">
        <ItinerarySidePanelMobile />
      </div>
    </div>
  );
}

/**
 * Public component with Provider wrapper
 */
export function AgentSessionView_01(props: React.ComponentProps<'section'> & AgentSessionView_01Props) {
  return (
    <ItineraryPanelProvider>
      <AgentSessionView_Inner {...props} />
    </ItineraryPanelProvider>
  );
}
