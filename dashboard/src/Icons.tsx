import type { ReactNode } from "react";

const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
const I = (children: ReactNode, size = 18) => (
  <svg {...base} width={size} height={size} aria-hidden="true">
    {children}
  </svg>
);

export const IconList = () => I(<><path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.5" cy="6" r=".8" /><circle cx="3.5" cy="12" r=".8" /><circle cx="3.5" cy="18" r=".8" /></>);
export const IconFolder = () => I(<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />);
export const IconBookmark =() => I(<path d="M6 3h12v18l-6-4-6 4z" />);
export const IconPlay = () => I(<path d="M7 4.5v15l12-7.5z" fill="currentColor" />, 16);
export const IconStop = () => I(<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />, 16);
export const IconExternal = () => I(<><path d="M14 4h6v6" /><path d="M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>, 15);
export const IconSearch = () => I(<><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>, 16);
export const IconCheck = () => I(<path d="M5 12.5l4.5 4.5L19 7.5" />, 16);
export const IconX = () => I(<path d="M6 6l12 12M18 6L6 18" />, 18);
export const IconChevron = () => I(<path d="M9 6l6 6-6 6" />, 16);
export const IconRefresh = () => I(<><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></>, 16);
export const IconLogout = () => I(<><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" /><path d="M10 17l-5-5 5-5M5 12h11" /></>, 16);
export const IconTerminal = () => I(<><path d="M4 5h16v14H4z" /><path d="M8 10l3 2-3 2M13 15h3" /></>, 16);
export const IconAlert = () => I(<><path d="M12 4l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>, 15);
export const IconDoc = () => I(<><path d="M7 3h7l5 5v13H7z" /><path d="M14 3v5h5M10 13h6M10 17h6" /></>);
export const IconUser = () => I(<><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></>);
