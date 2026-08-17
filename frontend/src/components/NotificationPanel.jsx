/**
 * NotificationPanel — bell icon in the header that opens the
 * Notifications tab in the footer status bar.
 */
import React from 'react';
import { Bell } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useVisibleNotifications } from '../api/hooks';

export default function NotificationPanel() {
  const { t } = useTranslation();
  // Shared TanStack Query cache entry with LogsFooter — one 30s poll,
  // minus the notes the user has dismissed (the badge must agree with
  // what the footer tab actually shows).
  // Query data is undefined during the first render (and while a cache entry
  // is being replaced). Keep the header usable until the first response lands.
  const { notifications: notifs = [] } = useVisibleNotifications() || {};
  const count = notifs.length;
  const badgeCount = count > 99 ? '99+' : count;
  const hasErrors = notifs.some((n) => n.level === 'error');
  const hasWarns = notifs.some((n) => n.level === 'warn');
  const badgeTone = hasErrors ? 'bg-danger' : hasWarns ? 'bg-warn' : 'bg-info';

  const openNotifications = () => {
    window.dispatchEvent(new CustomEvent('omni:open-notifications'));
  };

  return (
    <button
      type="button"
      className={`relative flex h-[28px] w-[28px] shrink-0 cursor-pointer items-center justify-center rounded-md border-0 bg-transparent p-0 transition-all duration-[0.15s] hover:bg-[rgba(255,255,255,0.04)] hover:text-fg ${count > 0 ? 'text-brand' : 'text-fg-muted'}`}
      onClick={openNotifications}
      aria-label={t('logs.notifications_count', { count })}
      title={t('logs.notifications')}
    >
      <Bell size={14} />
      {count > 0 && (
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute -top-[4px] -right-[4px] flex h-[14px] min-w-[14px] items-center justify-center rounded-[7px] px-[3px] font-mono text-[9px] font-bold leading-none text-white shadow-[0_1px_3px_rgba(0,0,0,0.4)] ${badgeTone}`}
        >
          {badgeCount}
        </span>
      )}
    </button>
  );
}
