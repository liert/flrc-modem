import React, { useEffect, useRef } from 'react';

const STEPS = Array.from({ length: 12 }, (_, index) => 10 ** (11 - index));
const MAX_FREQUENCY = 999_999_999_999;

export function SpectrumFrequencyTuner({ value, disabled = false, onChange }: {
  value: number;
  disabled?: boolean;
  onChange: (frequency: number) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const latestRef = useRef({ value, disabled, onChange });
  latestRef.current = { value, disabled, onChange };

  const adjust = (step: number, direction: number) => {
    const current = latestRef.current;
    if (current.disabled) return;
    const next = Math.max(1, Math.min(MAX_FREQUENCY, current.value + step * direction));
    if (next === current.value) return;
    // Several wheel events can arrive before React renders the next value.
    latestRef.current = { ...current, value: next };
    current.onChange(next);
  };

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onWheel = (event: WheelEvent) => {
      const digit = (event.target as HTMLElement).closest<HTMLElement>('[data-frequency-step]');
      if (!digit || !root.contains(digit) || latestRef.current.disabled || !event.deltaY || event.ctrlKey) return;
      event.preventDefault();
      event.stopPropagation();
      adjust(Number(digit.dataset.frequencyStep), event.deltaY < 0 ? 1 : -1);
    };
    // A non-passive listener prevents tuning from also scrolling the page.
    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, []);

  return <div ref={rootRef} role="group" aria-label="中心频率" className="spec-frequency-tuner">
    {STEPS.map((step, index) => {
      const digit = Math.floor(value / step) % 10;
      return <React.Fragment key={step}>
        {index > 0 && index % 3 === 0 && <span className="spec-frequency-dot" aria-hidden="true">.</span>}
        <button id={`spectrum-frequency-${index}`} type="button" role="spinbutton"
          aria-label={`频率第 ${index + 1} 位`} aria-valuemin={0} aria-valuemax={9} aria-valuenow={digit}
          data-frequency-step={step} disabled={disabled}
          title="上半部点击增加，下半部点击减少；滚轮向上增加、向下减少"
          onClick={event => {
            const bounds = event.currentTarget.getBoundingClientRect();
            adjust(step, event.clientY < bounds.top + bounds.height / 2 ? 1 : -1);
          }}
          onKeyDown={event => {
            if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
            event.preventDefault(); event.stopPropagation();
            adjust(step, event.key === 'ArrowUp' ? 1 : -1);
          }}>
          {digit}
        </button>
      </React.Fragment>;
    })}
  </div>;
}
