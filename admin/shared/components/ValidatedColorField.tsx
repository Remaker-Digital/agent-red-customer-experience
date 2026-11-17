// © 2026 Remaker Digital, a DBA of VanDusen & Palmeter, LLC. All rights reserved.

import { useEffect, useState } from 'react';
import { ColorInput } from '@mantine/core';

export function ValidatedColorField({
  label,
  value,
  onChange,
  allowEmpty = true,
  withAlpha = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  allowEmpty?: boolean;
  withAlpha?: boolean;
}) {
  const [input, setInput] = useState(value);
  useEffect(() => setInput(value), [value]);

  const colorPattern = withAlpha ? /^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/i : /^#[0-9a-f]{6}$/i;

  return (
    <ColorInput
      label={label}
      placeholder={allowEmpty ? 'Use theme default' : withAlpha ? '#RRGGBBAA' : '#RRGGBB'}
      format={withAlpha ? 'hexa' : 'hex'}
      value={input}
      eyeDropperButtonProps={{ 'aria-label': `Pick a screen color for the ${label.toLowerCase()}` }}
      onChange={(next) => {
        setInput(next);
        if (colorPattern.test(next) || (allowEmpty && next === '')) onChange(next);
      }}
      onBlur={() => setInput(value)}
    />
  );
}
