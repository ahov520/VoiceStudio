import React from 'react';
import Button from './Button.jsx';

/**
 * EmptyState — the "nothing here yet" placeholder: an icon, a title, an
 * optional description, and an optional call-to-action.
 *
 * Purely presentational — the wrapper is non-interactive; focus, keyboard,
 * disabled and loading states belong to the CTA `Button`, which already
 * carries them. Entrance is a token-timed fade/slide (`fadeIn` keyframes,
 * `--dur-base`/`--ease-out`) that collapses to nothing under
 * `prefers-reduced-motion`.
 *
 * @param icon        lucide icon component for the glyph slot (optional)
 * @param title       heading text (required)
 * @param description supporting line(s) (optional)
 * @param action      optional CTA: `{ label, onClick, leading?, loading?, disabled? }`
 * @param size        'sm' | 'md' — 'sm' for panels/cards, 'md' for whole pages
 */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  size = 'md',
  className = '',
  ...rest
}) {
  const sm = size === 'sm';
  return (
    <div
      className={`ui-empty-state flex flex-col items-center justify-center text-center ${
        sm
          ? 'gap-[var(--space-2)] px-[var(--space-4)] py-[var(--space-4)]'
          : 'gap-[var(--space-3)] px-[var(--space-6)] py-[var(--space-8)]'
      } [animation:fadeIn_var(--dur-base)_var(--ease-out)_both] motion-reduce:[animation:none] ${className}`}
      {...rest}
    >
      {Icon && (
        <Icon size={sm ? 20 : 28} strokeWidth={1.5} aria-hidden="true" className="text-fg-subtle" />
      )}
      <div
        className={`font-semibold text-fg ${sm ? 'text-[var(--text-sm)]' : 'text-[var(--text-md)]'}`}
      >
        {title}
      </div>
      {description && (
        <p
          className={`m-0 max-w-[420px] leading-[1.5] text-fg-muted ${
            sm ? 'text-[var(--text-xs)]' : 'text-[var(--text-sm)]'
          }`}
        >
          {description}
        </p>
      )}
      {action && <EmptyStateAction action={action} />}
    </div>
  );
}

/** CTA button — `label` becomes the child; every other key (variant,
    disabled, leading, …) passes straight through to `Button`. */
function EmptyStateAction({ action }) {
  const { label, ...buttonProps } = action;
  return (
    <Button variant="subtle" size="sm" className="mt-[var(--space-2)]" {...buttonProps}>
      {label}
    </Button>
  );
}
