import { useState, useEffect, useCallback, useRef } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';
import { analytics } from '../services/analytics';

export interface TourStep {
  targetSelector: string;
  content: string;
  placement?: 'top' | 'bottom' | 'left' | 'right';
  onBeforeStep?: () => void | Promise<void>;
  onAfterStep?: () => void;
}

interface GuidedTourProps {
  steps: TourStep[];
  onComplete: () => void;
  onSkip: () => void;
}

interface Point { x: number; y: number }
interface Rect { left: number; top: number; width: number; height: number }

const SPOT_PAD = 6;
const GAP = 16;
const MARGIN = 16;

async function waitForElement(selector: string, timeout = 2000): Promise<Element | null> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const el = document.querySelector(selector);
    if (el) return el;
    await new Promise(r => requestAnimationFrame(r));
  }
  return null;
}

function getArrowEndpoints(
  target: Rect,
  tipLeft: number,
  tipTop: number,
  tipW: number,
  tipH: number,
  placement: string,
): { from: Point; to: Point } {
  const pad = SPOT_PAD;
  let from: Point;
  let to: Point;

  switch (placement) {
    case 'top':
      from = { x: tipLeft + tipW / 2, y: tipTop + tipH + 2 };
      to = { x: target.left + target.width / 2, y: target.top - pad };
      break;
    case 'bottom':
      from = { x: tipLeft + tipW / 2, y: tipTop - 2 };
      to = { x: target.left + target.width / 2, y: target.top + target.height + pad };
      break;
    case 'left':
      from = { x: tipLeft + tipW + 2, y: tipTop + tipH / 2 };
      to = { x: target.left - pad, y: target.top + target.height / 2 };
      break;
    case 'right':
      from = { x: tipLeft - 2, y: tipTop + tipH / 2 };
      to = { x: target.left + target.width + pad, y: target.top + target.height / 2 };
      break;
    default:
      from = { x: tipLeft + tipW / 2, y: tipTop + tipH + 2 };
      to = { x: target.left + target.width / 2, y: target.top - pad };
  }
  return { from, to };
}

function HandDrawnArrow({ from, to }: { from: Point; to: Point }) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);
  if (dist < 10) return null;

  const bend = Math.min(dist * 0.2, 30);
  const nx = -dy / dist;
  const ny = dx / dist;

  const cp1: Point = {
    x: from.x + dx * 0.3 + nx * bend * 0.7,
    y: from.y + dy * 0.3 + ny * bend * 0.7,
  };
  const cp2: Point = {
    x: from.x + dx * 0.7 + nx * bend * 0.3,
    y: from.y + dy * 0.7 + ny * bend * 0.3,
  };

  const curve = `M ${from.x} ${from.y} C ${cp1.x} ${cp1.y}, ${cp2.x} ${cp2.y}, ${to.x} ${to.y}`;

  const angle = Math.atan2(to.y - cp2.y, to.x - cp2.x);
  const aLen = 12;
  const aAngle = Math.PI / 5.5;
  const a1: Point = {
    x: to.x - aLen * Math.cos(angle - aAngle),
    y: to.y - aLen * Math.sin(angle - aAngle),
  };
  const a2: Point = {
    x: to.x - aLen * Math.cos(angle + aAngle),
    y: to.y - aLen * Math.sin(angle + aAngle),
  };
  const head = `M ${a1.x} ${a1.y} L ${to.x} ${to.y} L ${a2.x} ${a2.y}`;

  return (
    <svg className="guided-tour-arrow">
      <path d={curve} stroke="#fff" strokeWidth={2.5} fill="none" strokeLinecap="round" />
      <path d={head} stroke="#fff" strokeWidth={2.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function GuidedTour({ steps, onComplete, onSkip }: GuidedTourProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [targetRect, setTargetRect] = useState<Rect | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ left: number; top: number } | null>(null);
  const [tooltipSize, setTooltipSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [resolvedPlacement, setResolvedPlacement] = useState<string>('bottom');
  const [ready, setReady] = useState(false);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const isMobile = useIsMobile();

  const step = steps[currentStep];
  const isFirst = currentStep === 0;
  const isLast = currentStep === steps.length - 1;

  useEffect(() => {
    analytics.track('guide_started', { total_steps: steps.length });
  }, [steps.length]);

  const updatePositions = useCallback(() => {
    const el = document.querySelector(step.targetSelector);
    if (!el) { setReady(false); return; }

    const rect = el.getBoundingClientRect();
    const tRect: Rect = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
    setTargetRect(tRect);

    const tooltip = tooltipRef.current;
    if (!tooltip) return;

    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    setTooltipSize({ w: tw, h: th });
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let placement = step.placement || 'bottom';
    let left: number;
    let top: number;

    if (isMobile) {
      left = MARGIN;
      const spaceBelow = vh - rect.bottom - SPOT_PAD;
      const spaceAbove = rect.top - SPOT_PAD;
      if (spaceBelow >= th + GAP) {
        placement = 'bottom';
        top = rect.bottom + SPOT_PAD + GAP;
      } else if (spaceAbove >= th + GAP) {
        placement = 'top';
        top = rect.top - SPOT_PAD - GAP - th;
      } else {
        placement = 'bottom';
        top = rect.bottom + SPOT_PAD + GAP;
      }
    } else {
      switch (placement) {
        case 'top': {
          left = rect.left + rect.width / 2 - tw / 2;
          top = rect.top - SPOT_PAD - GAP - th;
          if (top < MARGIN) { placement = 'bottom'; top = rect.bottom + SPOT_PAD + GAP; }
          break;
        }
        case 'bottom': {
          left = rect.left + rect.width / 2 - tw / 2;
          top = rect.bottom + SPOT_PAD + GAP;
          if (top + th > vh - MARGIN) { placement = 'top'; top = rect.top - SPOT_PAD - GAP - th; }
          break;
        }
        case 'left': {
          left = rect.left - SPOT_PAD - GAP - tw;
          top = rect.top + rect.height / 2 - th / 2;
          if (left < MARGIN) { placement = 'right'; left = rect.right + SPOT_PAD + GAP; }
          break;
        }
        case 'right': {
          left = rect.right + SPOT_PAD + GAP;
          top = rect.top + rect.height / 2 - th / 2;
          if (left + tw > vw - MARGIN) { placement = 'left'; left = rect.left - SPOT_PAD - GAP - tw; }
          break;
        }
        default: {
          left = rect.left + rect.width / 2 - tw / 2;
          top = rect.bottom + SPOT_PAD + GAP;
        }
      }
      left = Math.max(MARGIN, Math.min(left, vw - tw - MARGIN));
    }
    top = Math.max(MARGIN, Math.min(top!, vh - th - MARGIN));

    setTooltipPos({ left, top });
    setResolvedPlacement(placement);
    setReady(true);
  }, [step, isMobile]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setReady(false);
      if (step.onBeforeStep) await step.onBeforeStep();
      const el = await waitForElement(step.targetSelector);
      if (cancelled || !el) return;

      el.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
      await new Promise(r => setTimeout(r, 120));
      if (cancelled) return;
      updatePositions();
    })();

    return () => { cancelled = true; };
  }, [currentStep, step, updatePositions]);

  useEffect(() => {
    if (!ready) return;
    const handle = () => updatePositions();
    window.addEventListener('resize', handle);
    window.addEventListener('scroll', handle, true);
    return () => {
      window.removeEventListener('resize', handle);
      window.removeEventListener('scroll', handle, true);
    };
  }, [ready, updatePositions]);

  const goNext = () => {
    if (step.onAfterStep) step.onAfterStep();
    if (isLast) {
      analytics.track('guide_completed', { total_steps: steps.length });
      onComplete();
    } else {
      const nextStep = currentStep + 1;
      analytics.track('guide_step_viewed', { step_index: nextStep, step_target: steps[nextStep]?.targetSelector });
      setCurrentStep(nextStep);
    }
  };

  const goPrev = () => {
    if (step.onAfterStep) step.onAfterStep();
    setCurrentStep(prev => Math.max(0, prev - 1));
  };

  const handleSkip = () => {
    if (step.onAfterStep) step.onAfterStep();
    analytics.track('guide_skipped', { step_index: currentStep, total_steps: steps.length });
    onSkip();
  };

  const pad = SPOT_PAD;
  const arrowPts = ready && targetRect && tooltipPos
    ? getArrowEndpoints(targetRect, tooltipPos.left, tooltipPos.top, tooltipSize.w, tooltipSize.h, resolvedPlacement)
    : null;

  return (
    <>
      <div className="guided-tour-click-blocker" />

      <svg className="guided-tour-overlay" width="100%" height="100%">
        <defs>
          <mask id="guided-tour-mask">
            <rect width="100%" height="100%" fill="white" />
            {targetRect && (
              <rect
                x={targetRect.left - pad}
                y={targetRect.top - pad}
                width={targetRect.width + pad * 2}
                height={targetRect.height + pad * 2}
                rx={8}
                fill="black"
              />
            )}
          </mask>
        </defs>
        <rect width="100%" height="100%" fill="rgba(0,0,0,0.55)" mask="url(#guided-tour-mask)" />
      </svg>

      {arrowPts && <HandDrawnArrow from={arrowPts.from} to={arrowPts.to} />}

      <div
        ref={tooltipRef}
        className="guided-tour-tooltip"
        style={{
          left: tooltipPos?.left ?? -9999,
          top: tooltipPos?.top ?? -9999,
          opacity: ready ? 1 : 0,
        }}
      >
        <div className="guided-tour-tooltip-content">{step.content}</div>
        <div className="guided-tour-tooltip-actions">
          <button className="guided-tour-btn-skip" onClick={handleSkip}>
            {'\u8df3\u8fc7'}
          </button>
          <div style={{ flex: 1 }} />
          {!isFirst && (
            <button className="guided-tour-btn-prev" onClick={goPrev}>
              {'\u4e0a\u4e00\u6b65'}
            </button>
          )}
          <button className="guided-tour-btn-next" onClick={goNext}>
            {isLast ? '\u5b8c\u6210' : '\u4e0b\u4e00\u6b65'}
          </button>
        </div>
      </div>
    </>
  );
}
