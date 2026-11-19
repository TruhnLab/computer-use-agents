// Inline icons: no icon font and no network, the console runs offline.

const stroke = {
  fill: 'none', stroke: 'currentColor', strokeLinecap: 'round', strokeLinejoin: 'round',
};

export const PointerIcon = ({ size = 17 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <path d="M3 2.2l9.8 4.2-4.1 1.5-1.5 4.2z" />
  </svg>
);

export const CheckIcon = ({ size = 12 }) => (
  <svg width={size} height={size} viewBox="0 0 12 12" {...stroke} strokeWidth="2" aria-hidden="true">
    <path d="M2.5 6.3l2.3 2.3 4.7-5" />
  </svg>
);

export const CrossIcon = ({ size = 12 }) => (
  <svg width={size} height={size} viewBox="0 0 12 12" {...stroke} strokeWidth="2" aria-hidden="true">
    <path d="M3 3l6 6M9 3l-6 6" />
  </svg>
);

export const SpinnerIcon = ({ size = 12 }) => (
  <svg className="spin" width={size} height={size} viewBox="0 0 12 12" {...stroke} strokeWidth="2" aria-hidden="true">
    <path d="M6 1.5A4.5 4.5 0 1 1 1.5 6" />
  </svg>
);

export const PauseIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
    <rect x="2.5" y="2" width="3" height="10" rx="1" />
    <rect x="8.5" y="2" width="3" height="10" rx="1" />
  </svg>
);

export const PlayIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 14 14" fill="currentColor" aria-hidden="true">
    <path d="M4 2.2v9.6l7.6-4.8z" />
  </svg>
);

export const StopIcon = ({ size = 12 }) => (
  <svg width={size} height={size} viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
    <rect x="1" y="1" width="10" height="10" rx="2" />
  </svg>
);

export const SettingsIcon = ({ size = 20 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth="1.5" aria-hidden="true">
    <path d="M2 5h7.5M13.5 5h.5M2 11h.5M6.5 11H14" />
    <circle cx="11.5" cy="5" r="1.7" />
    <circle cx="4.5" cy="11" r="1.7" />
  </svg>
);

export const SendIcon = ({ size = 18 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth="1.75" aria-hidden="true">
    <path d="M8 13V3M4 7l4-4 4 4" />
  </svg>
);

export const ChevronIcon = ({ size = 16, open = false }) => (
  <svg className={`chevron${open ? ' open' : ''}`} width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth="1.75" aria-hidden="true">
    <path d="M3.5 6l4.5 4.5L12.5 6" />
  </svg>
);

export const ReplanIcon = ({ size = 13 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth="1.9" aria-hidden="true">
    <path d="M13 8a5 5 0 1 1-1.6-3.7" />
    <path d="M13 2.4v2.7h-2.7" />
  </svg>
);

export const CloseIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" {...stroke} strokeWidth="1.75" aria-hidden="true">
    <path d="M4 4l8 8M12 4l-8 8" />
  </svg>
);
