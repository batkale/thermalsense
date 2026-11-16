/**
 * Line icons shared by the map app and the landing page.
 *
 * Drawn here rather than taken from a set, and never emoji: ☀ and ✈ render as
 * colour emoji on Windows and as text glyphs elsewhere, so the same button
 * looked different on every machine. All of these take currentColor and are
 * aria-hidden — the control they sit in carries the label.
 */

const base = {
  xmlns: 'http://www.w3.org/2000/svg',
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: 'false',
};

/**
 * The ThermalSense mark: a thermal seen from above — the circle a glider flies
 * in it — with the glider on it, wing across its track. The circle is the thing
 * the product looks for; the glider is how it finds it.
 * Mirrored in the favicon data URIs of index.html and about.html.
 */
export function BrandMark({ size = 22, className }) {
  return (
    <svg {...base} className={className} width={size} height={size} viewBox="0 0 24 24" strokeWidth="1.7">
      <path d="M8.2 6.2A7.5 7.5 0 1 0 15.8 6.2" strokeDasharray="0.1 3" strokeWidth="2.1" />
      <path d="M12 1.8v8.4M9 6h6M9 4.9v2.2" />
      <circle cx="12" cy="13.6" r="1.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function SunIcon({ size = 15 }) {
  return (
    <svg {...base} width={size} height={size} viewBox="0 0 24 24" strokeWidth="1.8">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M5.3 18.7l1.5-1.5M17.2 6.8l1.5-1.5" />
    </svg>
  );
}

export function MoonIcon({ size = 15 }) {
  return (
    <svg {...base} width={size} height={size} viewBox="0 0 24 24" strokeWidth="1.8">
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
    </svg>
  );
}

/** Top view of a glider: long wing, fuselage, tailplane. */
export function GliderIcon({ size = 14 }) {
  return (
    <svg {...base} width={size} height={size} viewBox="0 0 24 24" strokeWidth="1.8">
      <path d="M1.5 9.5h21M12 5v15M8.5 19.5h7" />
    </svg>
  );
}

export function DownloadIcon({ size = 14 }) {
  return (
    <svg {...base} width={size} height={size} viewBox="0 0 24 24" strokeWidth="2">
      <path d="M12 4v11M7 10.5l5 5 5-5M5 20h14" />
    </svg>
  );
}

export function AlertIcon({ size = 13 }) {
  return (
    <svg {...base} width={size} height={size} viewBox="0 0 24 24" strokeWidth="2">
      <path d="M12 3.5 22 20.5H2L12 3.5Z" />
      <path d="M12 10v4.5M12 17.6v.1" />
    </svg>
  );
}
