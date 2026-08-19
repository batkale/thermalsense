// Dead reckoning between beacons.
//
// A marker sitting still is almost never the stream's fault. Measured on the
// live feed (19 Aug 2026, 1548 aircraft over 10 min): the feed delivers a
// beacon every 1.0 s median, but *per aircraft* the median gap is 3.9 s and the
// slowest tenth report only every 45 s. Between those beacons the backend has
// nothing new to send, so the map has nothing new to draw.
//
// So this projects the gap: advance the icon along its own heading at its own
// ground speed until the next real position arrives, then snap to that. The
// position is synthesised, which is why it is confined to the drawn icon and
// the click hit-test — never the pinned card's coordinate readout, and never
// the flight path, both of which must only ever show positions the aircraft
// actually reported.

// Straight-line projection is only defensible for a short gap. A glider at
// 100 km/h covers 420 m in 15 s; past that the odds of an unreported turn make
// the guess worse than an honestly stale icon, so extrapolation stops and the
// marker parks at the last known point.
export const MAX_EXTRAPOLATE_MS = 15000;

// Below this a "heading" is whatever noise the last fix left behind — a glider
// on the ground or thermalling at walking pace has no track to project along.
const MIN_SPEED_KMH = 5;

const M_PER_DEG_LAT = 111320;

/**
 * Where to draw this glider right now.
 *
 * Returns the reported position unchanged whenever projection would be a
 * guess rather than an inference: no receipt stamp, no usable heading or
 * speed, or a gap long enough that the aircraft could be anywhere.
 *
 * Circling aircraft are deliberately excluded. They are the ones this app
 * cares most about, and they are exactly the case a straight line gets wrong —
 * projecting a 20 s thermalling turn along its instantaneous heading walks the
 * icon out of the thermal it is climbing in, which is worse than not moving.
 *
 * @param {object} g   glider as sent on the wire, plus `rx` (ms epoch, when
 *                     this *position* first arrived — not when the frame did;
 *                     null until a position change has actually been observed,
 *                     since the wire carries no beacon timestamp)
 * @param {number} now Date.now()
 * @returns {{lat: number, lon: number}}
 */
export function displayPosition(g, now) {
  const here = { lat: g.lat, lon: g.lon };
  if (g.circling) return here;
  if (g.rx == null || g.heading == null || g.speed_kmh == null) return here;
  if (g.speed_kmh < MIN_SPEED_KMH) return here;

  const elapsed = now - g.rx;
  // Guard both ends: a negative age means the clock moved under us, and past
  // the cap we stop rather than extrapolate into fiction.
  if (!(elapsed > 0) || elapsed > MAX_EXTRAPOLATE_MS) return here;

  const metres = (g.speed_kmh / 3.6) * (elapsed / 1000);
  const rad    = (g.heading * Math.PI) / 180;   // 0 = north, clockwise

  // cos(lat) shrinks a degree of longitude away from the equator. Guard the
  // division: the feed is worldwide and a glider near the pole would otherwise
  // divide by ~0 and fly off the map.
  const cosLat = Math.max(0.01, Math.cos((g.lat * Math.PI) / 180));

  return {
    lat: g.lat + (metres * Math.cos(rad)) / M_PER_DEG_LAT,
    lon: g.lon + (metres * Math.sin(rad)) / (M_PER_DEG_LAT * cosLat),
  };
}

/**
 * True if any glider on screen is currently being projected.
 *
 * The render loop uses this to stay idle when it would only redraw identical
 * frames — a mapful of parked or thermalling gliders costs nothing until
 * something actually moves.
 */
export function anyExtrapolating(gliders, now) {
  return gliders.some(g => {
    const p = displayPosition(g, now);
    return p.lat !== g.lat || p.lon !== g.lon;
  });
}
