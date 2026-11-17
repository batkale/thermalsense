import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLang }  from '../i18n/LanguageContext.jsx';
import { useTheme } from '../theme/ThemeContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';
import { translatePitch } from './strings.js';
import { BrandMark, SunIcon, MoonIcon } from '../components/Icons.jsx';

const MAP_URL   = '/';
const ABOUT_URL = '/about';

// Published third-party results — see the header of strings.js for the full
// citations. Nothing here is a ThermalSense measurement.
const SOURCES = {
  nasaFlight: 'https://www.nasa.gov/news-release/nasa-experiment-explores-using-thermals-to-extend-uav-endurance/',
  nasaSim:    'https://ntrs.nasa.gov/citations/20050041655',
  autosoar:   'https://doi.org/10.1002/rob.21747',
};

// ThermalSense's own scores: the 16 Nov 2026 run quoted on /about (Landing.jsx
// SCORES and REGIONS). Change them there and here together.
const SCORES = [
  { key: 'rowTerrain', value: 0.525 },
  { key: 'rowSun',     value: 0.551 },
  { key: 'rowModel',   value: 0.570, emphasis: true },
  { key: 'rowIberia',  value: 0.611, emphasis: true },
  { key: 'rowAlps',    value: 0.612, emphasis: true },
];

// Reference points on the endurance curve, both from the publications above.
// AutoSOAR: ~3 h climbing of 7.8 h engaged. NASA simulation: 2 h -> 8.6 h is a
// 4.3x multiplier, which 1/(1-f) reaches at f = 1 - 1/4.3.
const REF_AUTOSOAR = 3 / 7.8;
const REF_NASA     = 1 - 2 / 8.6;
const F_MAX = 0.8;

const SECTIONS = ['problem', 'evidence', 'product', 'lale', 'gain', 'ask'];
const TOTAL = 9;

export default function Pitch() {
  const { lang, toggleLang, locale, t: tApp } = useLang();
  const { theme, toggleTheme } = useTheme();
  const t = useCallback((key, vars) => translatePitch(lang, key, vars), [lang]);

  useEffect(() => {
    document.title = t('docTitle');
    document.documentElement.lang = lang;
  }, [t, lang]);

  const num = useCallback(
    (v, digits = 1) => v.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }),
    [locale],
  );

  const langLabel  = lang === 'tr' ? tApp('switchToEnglish') : tApp('switchToTurkish');
  const themeLabel = theme === 'dark' ? tApp('switchToLight') : tApp('switchToDark');
  const navKey = { problem: 'navProblem', evidence: 'navEvidence', product: 'navProduct',
                   lale: 'navLale', gain: 'navGain', ask: 'navAsk' };

  return (
    <>
      <a className="skip" href="#main">{t('skipToContent')}</a>

      <header className="pitch-top">
        <div className="wrap nav">
          <a className="brand" href="#cover"><BrandMark className="brand-mark" size={22} />ThermalSense</a>
          <nav className="nav-links" aria-label={t('navLabel')}>
            {SECTIONS.map(id => <a key={id} href={`#${id}`}>{t(navKey[id])}</a>)}
          </nav>
          <div className="nav-tools">
            <button type="button" className="tool" onClick={toggleLang} title={langLabel} aria-label={langLabel}>
              {LANG_LABEL[lang]}
            </button>
            <button type="button" className="tool" onClick={toggleTheme} title={themeLabel} aria-label={themeLabel}>
              {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
            </button>
            <a className="btn primary small" href={MAP_URL}>{t('openMapShort')}</a>
          </div>
        </div>
      </header>

      <main id="main">
        {/* 01 — cover */}
        <section className="slide cover" id="cover" aria-labelledby="cover-h">
          <div className="wrap">
            <Counter n={1} t={t} />
            <p className="eyebrow">{t('eyebrow')}</p>
            <h1 id="cover-h" className="cover-title">
              <span className="thin">{t('coverA')}</span>
              <span className="bold">{t('coverB')}</span>
            </h1>
            <p className="lede">{t('coverLede')}</p>
            <div className="actions">
              <a className="btn primary" href="#gain">{t('coverCta')}</a>
              <a className="btn quiet" href={MAP_URL}>{t('openMap')}</a>
            </div>
            <p className="status"><span className="live-dot on" aria-hidden="true" />{t('coverStatus')}</p>
          </div>
        </section>

        {/* 02 — problem */}
        <section className="slide" id="problem" aria-labelledby="problem-h">
          <div className="wrap split">
            <div>
              <Counter n={2} t={t} />
              <h2 id="problem-h">{t('probTitle')}</h2>
              <p className="intro">{t('probText')}</p>
              <dl className="definition">
                <dt>{t('laleDefTerm')}</dt>
                <dd>{t('laleDef')}</dd>
              </dl>
            </div>
            <div className="points">
              <h3>{t('probPoints')}</h3>
              <ol>
                <li>{t('probP1')}</li>
                <li>{t('probP2')}</li>
                <li>{t('probP3')}</li>
              </ol>
            </div>
          </div>
        </section>

        {/* 03 — evidence */}
        <section className="slide" id="evidence" aria-labelledby="evidence-h">
          <div className="wrap">
            <Counter n={3} t={t} />
            <h2 id="evidence-h">{t('evTitle')}</h2>
            <p className="intro">{t('evIntro')}</p>
            <div className="evidence">
              <Evidence fig={t('ev1Fig')} who={t('ev1Who')} href={SOURCES.nasaFlight} src={t('evSource')}>{t('ev1Text')}</Evidence>
              <Evidence fig={t('ev2Fig')} who={t('ev2Who')} href={SOURCES.nasaSim} src={t('evSource')}>{t('ev2Text')}</Evidence>
              <Evidence fig={t('ev3Fig')} who={t('ev3Who')} href={SOURCES.autosoar} src={t('evSource')}>{t('ev3Text')}</Evidence>
            </div>
          </div>
        </section>

        {/* 04 — gap */}
        <section className="slide" aria-labelledby="gap-h">
          <div className="wrap split">
            <div>
              <Counter n={4} t={t} />
              <h2 id="gap-h">{t('gapTitle')}</h2>
              <p className="intro">{t('gapText')}</p>
            </div>
            <SearchFigure t={t} />
          </div>
        </section>

        {/* 05 — product */}
        <section className="slide" id="product" aria-labelledby="product-h">
          <div className="wrap">
            <Counter n={5} t={t} />
            <h2 id="product-h">{t('prodTitle')}</h2>
            <p className="intro">{t('prodIntro')}</p>
            <dl className="facts">
              <Fact figure="201 × 201" unit={t('factCells')}>{t('factCellsText')}</Fact>
              <Fact figure={t('feedFig')} unit={t('factFeed')}>{t('factFeedText')}</Fact>
              <Fact figure={t('retrainFig')} unit={t('factRetrain')}>{t('factRetrainText')}</Fact>
              <Fact figure={t('aheadFig')} unit={t('factAhead')}>{t('factAheadText')}</Fact>
            </dl>
            <div className="split score-split">
              <div>
                <h3>{t('scoreTitle')}</h3>
                <p>{t('scoreText')}</p>
                <p className="reading">{t('scoreHonest')}</p>
                <p className="meta">{t('scoreMeta')}</p>
              </div>
              <ScoreBars rows={SCORES} t={t} num={num} />
            </div>
          </div>
        </section>

        {/* 06 — LALE */}
        <section className="slide" id="lale" aria-labelledby="lale-h">
          <div className="wrap">
            <Counter n={6} t={t} />
            <h2 id="lale-h">{t('laleTitle')}</h2>
            <p className="intro">{t('laleIntro')}</p>
            <ol className="modes">
              <li><span className="mode-n">A</span><h3>{t('mode1')}</h3><p>{t('mode1Text')}</p></li>
              <li><span className="mode-n">B</span><h3>{t('mode2')}</h3><p>{t('mode2Text')}</p></li>
              <li className="later"><span className="mode-n">C</span><h3>{t('mode3')}</h3><p>{t('mode3Text')}</p></li>
            </ol>
            <h3 className="sub">{t('usesTitle')}</h3>
            <div className="notes uses">
              <div><h3>{t('use1')}</h3><p>{t('use1Text')}</p></div>
              <div><h3>{t('use2')}</h3><p>{t('use2Text')}</p></div>
              <div><h3>{t('use3')}</h3><p>{t('use3Text')}</p></div>
              <div><h3>{t('use4')}</h3><p>{t('use4Text')}</p></div>
            </div>
            <p className="meta acoustic">{t('acoustic')}</p>
          </div>
        </section>

        {/* 07 — gain */}
        <section className="slide" id="gain" aria-labelledby="gain-h">
          <div className="wrap">
            <Counter n={7} t={t} />
            <h2 id="gain-h">{t('gainTitle')}</h2>
            <p className="intro">{t('gainIntro')}</p>
            <GainCalculator t={t} num={num} />
            <div className="honest">
              <h3>{t('gainHonestTitle')}</h3>
              <p>{t('gainHonest')}</p>
            </div>
          </div>
        </section>

        {/* 08 — why us */}
        <section className="slide" aria-labelledby="why-h">
          <div className="wrap">
            <Counter n={8} t={t} />
            <h2 id="why-h">{t('whyTitle')}</h2>
            <div className="notes why">
              <div><h3>{t('why1')}</h3><p>{t('why1Text')}</p></div>
              <div><h3>{t('why2')}</h3><p>{t('why2Text')}</p></div>
              <div><h3>{t('why3')}</h3><p>{t('why3Text')}</p></div>
              <div><h3>{t('why4')}</h3><p>{t('why4Text')}</p></div>
            </div>
          </div>
        </section>

        {/* 09 — roadmap and ask */}
        <section className="slide ask" id="ask" aria-labelledby="ask-h">
          <div className="wrap split">
            <div>
              <Counter n={9} t={t} />
              <h2>{t('roadTitle')}</h2>
              <ol className="road">
                <li><span className="when">{t('road1When')}</span><span>{t('road1')}</span></li>
                <li><span className="when">{t('road2When')}</span><span>{t('road2')}</span></li>
                <li><span className="when">{t('road3When')}</span><span>{t('road3')}</span></li>
              </ol>
            </div>
            <div className="ask-box">
              <h2 id="ask-h">{t('askTitle')}</h2>
              <p className="intro">{t('askText')}</p>
              <div className="actions">
                <a className="btn primary" href={MAP_URL}>{t('askCta')}</a>
                <a className="btn quiet" href={ABOUT_URL}>{t('technical')}</a>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="close">
        <div className="wrap">
          <p className="disclaimer">{t('disclaimer')}</p>
        </div>
      </footer>
    </>
  );
}

function Counter({ n, t }) {
  return (
    <p className="counter">
      {t('slideOf', { n: String(n).padStart(2, '0'), total: String(TOTAL).padStart(2, '0') })}
    </p>
  );
}

function Evidence({ fig, who, href, src, children }) {
  return (
    <article className="ev">
      <p className="ev-fig">{fig}</p>
      <h3>{who}</h3>
      <p>{children}</p>
      <a className="ev-src" href={href} rel="noopener">{src} ↗</a>
    </article>
  );
}

function Fact({ figure, unit, children }) {
  return (
    <div className="fact">
      <dt>
        <span className="fact-figure">{figure}</span>
        <span className="fact-unit">{unit}</span>
      </dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Horizontal bars from the coin-toss line, so the eye reads the gap above 0.5. */
function ScoreBars({ rows, t, num }) {
  const lo = 0.5, hi = 0.65;
  return (
    <table className="bars">
      <caption className="sr-only">{t('scoreTitle')}</caption>
      <tbody>
        {rows.map(r => {
          const w = Math.max(0, (r.value - lo) / (hi - lo)) * 100;
          return (
            <tr key={r.key} className={r.emphasis ? 'em' : undefined}>
              <th scope="row">{t(r.key)}</th>
              <td className="bar-cell"><span className="bar" style={{ width: `${w}%` }} /></td>
              <td className="bar-val">{num(r.value, 3)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * Blind spiral search against a search started from a prior map, drawn on the
 * same patch of ground. Schematic: the dot is "a thermal", not a measured one.
 */
function SearchFigure({ t }) {
  const spiral = useMemo(() => {
    const pts = [];
    for (let a = 0; a <= Math.PI * 7; a += 0.12) {
      const r = 4 + a * 3.4;
      pts.push(`${(60 + r * Math.cos(a)).toFixed(1)},${(60 + r * Math.sin(a)).toFixed(1)}`);
    }
    return pts.join(' ');
  }, []);
  // A 6x6 prior: warmer cells toward the thermal at (88, 34).
  const cells = useMemo(() => {
    const out = [];
    for (let i = 0; i < 6; i++) for (let j = 0; j < 6; j++) {
      const cx = 10 + i * 20, cy = 10 + j * 20;
      const d = Math.hypot(cx - 88, cy - 34);
      out.push({ x: i * 20, y: j * 20, o: Math.max(0.05, 0.75 - d / 90) });
    }
    return out;
  }, []);
  return (
    <div className="search-fig">
      <figure>
        <svg viewBox="0 0 120 120" role="img" aria-label={t('gapBlindText')}>
          <rect x="0" y="0" width="120" height="120" className="sf-ground" />
          <polyline points={spiral} className="sf-path" />
          <circle cx="88" cy="34" r="7" className="sf-thermal" />
        </svg>
        <figcaption><strong>{t('gapBlind')}</strong><span>{t('gapBlindText')}</span></figcaption>
      </figure>
      <figure>
        <svg viewBox="0 0 120 120" role="img" aria-label={t('gapMapText')}>
          <rect x="0" y="0" width="120" height="120" className="sf-ground" />
          {cells.map((c, k) => (
            <rect key={k} x={c.x} y={c.y} width="20" height="20" className="sf-cell" style={{ opacity: c.o }} />
          ))}
          <polyline points="12,108 60,70 88,34" className="sf-path direct" />
          <circle cx="88" cy="34" r="7" className="sf-thermal" />
        </svg>
        <figcaption><strong>{t('gapMap')}</strong><span>{t('gapMapText')}</span></figcaption>
      </figure>
    </div>
  );
}

/** 1 / (1 - f): arithmetic on a stated assumption, not a measurement. */
function GainCalculator({ t, num }) {
  const [base, setBase] = useState(4);
  const [share, setShare] = useState(Math.round(REF_AUTOSOAR * 100));
  const f = share / 100;
  const mult = 1 / (1 - f);
  const hours = base * mult;

  // Chart geometry: f on x (0..F_MAX), multiplier on y (1..5).
  const W = 320, H = 200, P = { l: 34, r: 10, t: 12, b: 28 };
  const x = v => P.l + (v / F_MAX) * (W - P.l - P.r);
  const y = m => H - P.b - ((Math.min(m, 5) - 1) / 4) * (H - P.t - P.b);
  const curve = useMemo(() => {
    const pts = [];
    for (let v = 0; v <= F_MAX + 1e-9; v += 0.01) pts.push(`${x(v).toFixed(1)},${y(1 / (1 - v)).toFixed(1)}`);
    return pts.join(' ');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="gain">
      <div className="gain-controls">
        <label className="gain-field">
          <span className="gain-label">{t('gainBase')}</span>
          <span className="gain-value">{t('gainHours', { h: num(base, 0) })}</span>
          <input type="range" min="1" max="12" step="1" value={base}
                 onChange={e => setBase(Number(e.target.value))} />
        </label>
        <label className="gain-field">
          <span className="gain-label">{t('gainShare')}</span>
          <span className="gain-value">{t('pct', { n: share })}</span>
          <input type="range" min="0" max={F_MAX * 100} step="1" value={share}
                 onChange={e => setShare(Number(e.target.value))} />
        </label>
        <ul className="gain-refs">
          <li><button type="button" onClick={() => setShare(Math.round(REF_AUTOSOAR * 100))}>
            {t('gainRefAuto')} · {t('pct', { n: Math.round(REF_AUTOSOAR * 100) })}</button></li>
          <li><button type="button" onClick={() => setShare(Math.round(REF_NASA * 100))}>
            {t('gainRefNasa')} · {t('pct', { n: Math.round(REF_NASA * 100) })}</button></li>
        </ul>
        <p className="meta">{t('gainAssume')}</p>
      </div>

      <div className="gain-result" aria-live="polite">
        <p className="gain-label">{t('gainResult')}</p>
        <p className="gain-big">{t('gainHours', { h: num(hours, 1) })}</p>
        <p className="gain-sub">
          <span>{t('gainTimes', { x: num(mult, 2) })}</span>
          <span className="lift">{t('gainExtra', { h: num(hours - base, 1) })}</span>
        </p>
        <svg className="gain-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('gainChartAlt')}>
          {[1, 2, 3, 4, 5].map(m => (
            <g key={m}>
              <line x1={P.l} x2={W - P.r} y1={y(m)} y2={y(m)} className="gc-grid" />
              <text x={P.l - 6} y={y(m) + 4} className="gc-tick" textAnchor="end">{m}×</text>
            </g>
          ))}
          {[0, 0.2, 0.4, 0.6, 0.8].map(v => (
            <text key={v} x={x(v)} y={H - 10} className="gc-tick" textAnchor="middle">{t('pct', { n: Math.round(v * 100) })}</text>
          ))}
          <polyline points={curve} className="gc-curve" />
          <line x1={x(f)} x2={x(f)} y1={y(1)} y2={y(mult)} className="gc-drop" />
          <circle cx={x(f)} cy={y(mult)} r="5" className="gc-dot" />
        </svg>
      </div>
    </div>
  );
}
