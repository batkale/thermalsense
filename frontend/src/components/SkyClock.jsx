import { useEffect, useState } from 'react';

/**
 * The sky and the local clock at a point — shared by the top bar (the scanned
 * point, at the slider's moment) and the pinned glider card (the aircraft,
 * now).
 *
 * The backend sends `condition` as a key (clear, partly_cloudy, rain, …) and
 * `utc_offset_s` for the moment it answers for; both are null when the weather
 * is the synthetic fallback, and then nothing here claims a sky or a clock.
 */

/** Translation key for a condition. "Clear" by day reads as sunny. */
export function conditionKey(condition, isDay) {
  if (!condition) return null;
  if (condition === 'clear' && isDay !== false) return 'weather_cond_sunny';
  return `weather_cond_${condition}`;
}

/**
 * The time to show: the forecast moment when one was asked for, or a clock
 * that keeps running when the answer is for "now" — a frozen "now" on a page
 * left open for an hour is a wrong time, not an old one.
 */
export function useLocalClock(utcOffsetS, targetAt, live) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live || utcOffsetS == null) return undefined;
    const id = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(id);
  }, [live, utcOffsetS]);
  if (utcOffsetS == null) return null;
  const base = live || !targetAt ? now : Date.parse(targetAt);
  if (Number.isNaN(base)) return null;
  return base + utcOffsetS * 1000;
}

/**
 * Wall-clock text for a shifted epoch. Formatting in UTC is what makes it the
 * point's time rather than the browser's: the offset is already folded in.
 */
export function formatLocalTime(shiftedMs, locale, withDay = false) {
  if (shiftedMs == null) return null;
  return new Intl.DateTimeFormat(locale, {
    timeZone: 'UTC',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    ...(withDay ? { weekday: 'short' } : {}),
  }).format(new Date(shiftedMs));
}

/** "UTC+3", "UTC−5", "UTC+5:30". */
export function formatOffset(t, utcOffsetS) {
  if (utcOffsetS == null) return null;
  const sign = utcOffsetS < 0 ? '−' : '+';
  const abs = Math.abs(utcOffsetS);
  const h = Math.floor(abs / 3600);
  const m = Math.round((abs % 3600) / 60);
  return t('utcOffset', { sign, h, m: m ? `:${String(m).padStart(2, '0')}` : '' });
}

const base = {
  xmlns: 'http://www.w3.org/2000/svg',
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: 'false',
  viewBox: '0 0 24 24',
  strokeWidth: 1.7,
};

// A cloud whose flat base sits at y = 17, or 14 when weather falls from it.
const CLOUD     = 'M7 17h10a3.6 3.6 0 0 0 .3-7.2A5.2 5.2 0 0 0 7.4 9.6 3.75 3.75 0 0 0 7 17Z';
const CLOUD_UP  = 'M7 14h10a3.6 3.6 0 0 0 .3-7.2A5.2 5.2 0 0 0 7.4 6.6 3.75 3.75 0 0 0 7 14Z';
// Small sun / moon peeking from behind the cloud's upper left.
const SMALL_SUN = (
  <>
    <circle cx="8" cy="7.5" r="2.6" />
    <path d="M8 2.2v1.2M2.7 7.5h1.2M4.3 3.8l.85.85M11.7 3.8l-.85.85M4.3 11.2l.85-.85" />
  </>
);
const SMALL_MOON = <path d="M10.6 9.2A4 4 0 0 1 5.3 3.9a4 4 0 1 0 5.3 5.3Z" />;

/** Line icon for a condition. Same drawing rules as Icons.jsx. */
export function ConditionIcon({ condition, isDay, size = 16 }) {
  const day = isDay !== false;
  let body;
  switch (condition) {
    case 'clear':
      body = day
        ? (<>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.5 1.5M17.2 17.2l1.5 1.5M5.3 18.7l1.5-1.5M17.2 6.8l1.5-1.5" />
          </>)
        : <path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5Z" />;
      break;
    case 'mostly_clear':
    case 'partly_cloudy':
      body = (<>
        {day ? SMALL_SUN : SMALL_MOON}
        <path d="M9.5 20.5h8.5a3.2 3.2 0 0 0 .3-6.4A4.6 4.6 0 0 0 9.9 13.9 3.3 3.3 0 0 0 9.5 20.5Z" />
      </>);
      break;
    case 'cloudy':
      body = <path d={CLOUD} />;
      break;
    case 'fog':
      body = (<>
        <path d={CLOUD_UP} />
        <path d="M4 17.5h16M6 20.5h12" />
      </>);
      break;
    case 'drizzle':
      body = (<>
        <path d={CLOUD_UP} />
        <path d="M8 17.5v.6M12 18.5v.6M16 17.5v.6M10 20.8v.6M14 20.8v.6" />
      </>);
      break;
    case 'rain':
      body = (<>
        <path d={CLOUD_UP} />
        <path d="M8.5 16.5l-1 3M12.5 16.5l-1 3M16.5 16.5l-1 3" />
      </>);
      break;
    case 'snow':
      body = (<>
        <path d={CLOUD_UP} />
        <path d="M8 17v3M6.7 18.5h2.6M16 17v3M14.7 18.5h2.6M12 19.5v3M10.7 21h2.6" />
      </>);
      break;
    case 'thunderstorm':
      body = (<>
        <path d={CLOUD_UP} />
        <path d="M12.5 15l-2.5 4h3.2l-2 4" />
      </>);
      break;
    default:
      return null;
  }
  return <svg {...base} width={size} height={size} className="cond-icon">{body}</svg>;
}
