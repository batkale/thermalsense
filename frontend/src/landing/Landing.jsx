import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLang }  from '../i18n/LanguageContext.jsx';
import { useTheme } from '../theme/ThemeContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';
import { API_BASE } from '../config.js';
import { translateLanding } from './strings.js';
import ThermalField from './ThermalField.jsx';
import { BrandMark, SunIcon, MoonIcon } from '../components/Icons.jsx';

const MAP_URL      = '/';
const COMPARE_URL  = '/?compare=1';
const TRAINING_URL = '/?panel=training';

// Published third-party results — see the header of strings.js. Nothing here
// is a ThermalSense measurement.
const SOURCES = {
  nasaFlight: 'https://www.nasa.gov/news-release/nasa-experiment-explores-using-thermals-to-extend-uav-endurance/',
  nasaSim:    'https://ntrs.nasa.gov/citations/20050041655',
  autosoar:   'https://doi.org/10.1002/rob.21747',
};

// Bibliography. Citations are bibliographic data, identical in both languages,
// so they live here rather than in strings.js; the headings around them are
// translated. Every entry was checked against its publisher or index on
// 17 Nov 2026 — add a new one only with a resolvable link.
const REFS = [
  { cite: 'Allen, M. J. (2005). Autonomous Soaring for Improved Endurance of a Small Uninhabited Air Vehicle. 43rd AIAA Aerospace Sciences Meeting. NASA NTRS 20050041655.',
    href: 'https://ntrs.nasa.gov/citations/20050041655' },
  { cite: 'NASA (2005). NASA Experiment Explores Using Thermals to Extend UAV Endurance. NASA Dryden press release.',
    href: SOURCES.nasaFlight },
  { cite: 'Allen, M. J., & Lin, V. (2007). Guidance and Control of an Autonomous Soaring UAV. NASA/TM-2007-214611.',
    href: 'https://ntrs.nasa.gov/citations/20070022339' },
  { cite: 'Edwards, D. J., & Silberberg, L. M. (2010). Autonomous Soaring: The Montague Cross-Country Challenge. Journal of Aircraft, 47(5), 1763–1769.',
    href: 'https://doi.org/10.2514/1.C000287' },
  { cite: 'Depenbusch, N. T., Bird, J. J., & Langelaan, J. W. (2018). The AutoSOAR autonomous soaring aircraft, part 2: Hardware implementation and flight results. Journal of Field Robotics, 35(4).',
    href: SOURCES.autosoar },
  { cite: 'Tabor, S., Guilliard, I., & Kolobov, A. (2018). ArduSoar: An Open-Source Thermalling Controller for Resource-Constrained Autopilots. arXiv:1802.08215.',
    href: 'https://arxiv.org/abs/1802.08215' },
  { cite: 'Lenschow, D. H., & Stephens, P. L. (1980). The role of thermals in the convective boundary layer. Boundary-Layer Meteorology, 19, 509–532.',
    href: 'https://doi.org/10.1007/BF00122351' },
  { cite: 'Ákos, Z., Nagy, M., & Vicsek, T. (2008). Comparing bird and human soaring strategies. PNAS, 105(11), 4139–4143.',
    href: 'https://doi.org/10.1073/pnas.0707711105' },
  { cite: 'Mordor Intelligence (2026). Unmanned Aerial Vehicles (UAV) Market — Outlook 2026–2031. Press release, 7 April 2026.',
    href: 'https://www.bolsamania.com/nota-de-prensa/mercados/unmanned-aerial-vehicles-uav-market-outlook-2026-2031-led-by-the-fixed-wing-segment-with-7478-share-in-2025---mordor-intelligence--22250897.html' },
  { cite: 'Mordor Intelligence (2026). Small UAV Market — Share Analysis, Industry Trends & Statistics, Growth Forecasts. 28 July 2026.',
    href: 'https://www.giiresearch.com/report/moi1850151-small-uav-market-share-analysis-industry-trends.html' },
];

// ThermalSense's own scores, within-(day, cell, hour) micro AUC:
// `python -m evaluation.evaluate --variant base --by-hour`, run 16 Nov 2026 on
// the 6 Nov 2026 buffer minus its invented-weather rows (CLAUDE.md). The CIs
// are quoted in strings.js scoreMeta; change both together.
const SCORES = [
  { key: 'rowTerrain', value: 0.525 },
  { key: 'rowSun',     value: 0.551 },
  { key: 'rowModel',   value: 0.570, emphasis: true },
  { key: 'rowIberia',  value: 0.611, emphasis: true },
  { key: 'rowAlps',    value: 0.612, emphasis: true },
];

// Forecast ledger, GET /verification/summary?days=30 on the live node, read
// 17 Nov 2026 (rules_version 1). [value, lo, hi]; n = forecasts that had
// traffic to score against. pooled = pooled_served, hit = hit_rate (chance
// 0.2), within = within_served. 360 was added to LEDGER_HORIZONS_MIN that day
// and has no result yet — it stays "being measured" until it does.
const HORIZONS = [
  { min: 15,  pooled: [0.714, 0.671, 0.753], hit: [0.254, 0.213, 0.307], within: [0.530, 0.490, 0.577], n: 199 },
  { min: 30,  pooled: [0.683, 0.632, 0.731], hit: [0.243, 0.198, 0.292], within: [0.502, 0.454, 0.558], n: 149 },
  { min: 60,  pooled: [0.701, 0.643, 0.753], hit: [0.220, 0.165, 0.272], within: [0.477, 0.425, 0.530], n: 119 },
  { min: 120, pooled: [0.709, 0.626, 0.784], hit: [0.143, 0.080, 0.215], within: [0.468, 0.391, 0.546], n: 68 },
  { min: 180, pooled: [0.671, 0.559, 0.808], hit: [0.132, 0.067, 0.208], within: [0.482, 0.354, 0.619], n: 51 },
  { min: 360, pending: true },
];

// Competition matrix: one row per question, one cell per column, each a
// strings.js key. Claims about other products are limited to what their own
// pages show; "none found" is the honest form of "they do not have it".
const CMP_COLS = ['cmpColUs', 'cmpColSoar', 'cmpColNwp', 'cmpColOnboard', 'cmpColTrad'];
const CMP_ROWS = [
  ['cmpRowPre',   ['cmpYes', 'cmpYes', 'cmpPartial', 'cmpNo', 'cmpNo']],
  ['cmpRowRes',   ['cmpResUs', 'cmpResNwp', 'cmpResNwp', 'cmpResOnboard', 'cmpDash']],
  ['cmpRowLearn', ['cmpLearnUs', 'cmpLearnNone', 'cmpLearnNone', 'cmpLearnOnboard', 'cmpLearnTrad']],
  ['cmpRowHw',    ['cmpNotNeeded', 'cmpNotNeeded', 'cmpNotNeeded', 'cmpNotNeeded', 'cmpNotNeeded']],
  ['cmpRowAudit', ['cmpAuditUs', 'cmpAuditNone', 'cmpAuditNone', 'cmpDash', 'cmpDash']],
  ['cmpRowUser',  ['cmpUserUs', 'cmpUserSoar', 'cmpUserNwp', 'cmpUserOnboard', 'cmpUserTrad']],
  ['cmpRowPrice', ['cmpPriceUs', 'cmpPriceSoar', 'cmpPriceNwp', 'cmpPriceOnboard', 'cmpDash']],
];

// Market: TAM / SAM / SOM. Figures and their sources are in strings.js.
const MARKET = ['tam', 'sam', 'som'];

// Reference points on the flight-time curve, both from the publications above.
// AutoSOAR: ~3 h climbing of 7.8 h engaged. NASA simulation: 2 h -> 8.6 h is a
// 4.3x multiplier, which 1/(1-f) reaches at f = 1 - 1/4.3.
const REF_AUTOSOAR = 3 / 7.8;
const REF_NASA     = 1 - 2 / 8.6;
const F_MAX = 0.8;

const SECTIONS = ['problem', 'product', 'architecture', 'horizon', 'competition', 'market', 'gain', 'roadmap'];
const NAV_KEY = {
  problem: 'navProblem', product: 'navProduct', architecture: 'navArch', horizon: 'navHorizon',
  competition: 'navCompete', market: 'navMarket', gain: 'navGain', roadmap: 'navRoad',
};
const TOTAL = 13;

/**
 * How many thermals the feed is marking right now — the one live number on the
 * page. null means "leave the line out": a page making a case must never look
 * broken because the API is busy.
 */
function useActiveThermals() {
  const [count, setCount] = useState(null);
  useEffect(() => {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 6000);
    fetch(`${API_BASE}/thermals/active`, { signal: ctl.signal })
      .then(res => (res.ok ? res.json() : null))
      .then(data => { if (Array.isArray(data?.thermals)) setCount(data.thermals.length); })
      .catch(() => { /* leave the line out */ })
      .finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); ctl.abort(); };
  }, []);
  return count;
}

export default function Landing() {
  const { lang, toggleLang, locale, t: tApp } = useLang();
  const { theme, toggleTheme } = useTheme();
  const t = useCallback((key, vars) => translateLanding(lang, key, vars), [lang]);
  const live = useActiveThermals();

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

  return (
    <>
      <a className="skip" href="#main">{t('skipToContent')}</a>

      <header className="pitch-top">
        <div className="wrap nav">
          <a className="brand" href="#cover"><BrandMark className="brand-mark" size={22} />ThermalSense</a>
          <nav className="nav-links" aria-label={t('navLabel')}>
            {SECTIONS.map(id => <a key={id} href={`#${id}`}>{t(NAV_KEY[id])}</a>)}
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
              <a className="btn primary" href={MAP_URL}>{t('openMap')}</a>
              <a className="btn quiet" href="#gain">{t('coverCta')}</a>
            </div>
            <p className="status"><span className="live-dot on" aria-hidden="true" />{t('coverStatus')}</p>
            {live != null && (
              <p className="status live-count">{live > 0 ? t('liveNow', { n: live }) : t('liveNone')}</p>
            )}
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
                <dt>{t('classDefTerm')}</dt>
                <dd>{t('classDef')}</dd>
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
            <figure className="field-figure">
              <ThermalField t={t} />
              <figcaption>{t('simCaption')}</figcaption>
            </figure>
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
            <div className="demo-call">
              <div>
                <h3>{t('demoTitle')}</h3>
                <p>{t('demoText')}</p>
              </div>
              <a className="btn primary" href={COMPARE_URL}>{t('openCompare')}</a>
            </div>
          </div>
        </section>

        {/* 06 — architecture: where the AI is, and what runs offline */}
        <section className="slide" id="architecture" aria-labelledby="arch-h">
          <div className="wrap">
            <Counter n={6} t={t} />
            <h2 id="arch-h">{t('archTitle')}</h2>
            <p className="intro">{t('archIntro')}</p>
            <div className="pipeline">
              <p className="pipe-zone online">{t('archOnline')}</p>
              <p className="pipe-zone offline">{t('archOffline')}</p>
              <ol className="pipe">
                {[1, 2, 3, 4].map(i => (
                  <li key={i} className={`pipe-step s${i}${i === 2 ? ' ai' : ''}`}>
                    <span className="pipe-tag">{t(`arch${i}Tag`)}</span>
                    <h3>{t(`arch${i}`)}</h3>
                    <p>{t(`arch${i}Text`)}</p>
                  </li>
                ))}
              </ol>
            </div>
            <p className="meta alt-ref">{t('archAltRef')}</p>
            <h3 className="sub">{t('opsTitle')}</h3>
            <ol className="modes">
              <li><span className="mode-n">1</span><h3>{t('ops1')}</h3><p>{t('ops1Text')}</p></li>
              <li><span className="mode-n">2</span><h3>{t('ops2')}</h3><p>{t('ops2Text')}</p></li>
              <li><span className="mode-n">3</span><h3>{t('ops3')}</h3><p>{t('ops3Text')}</p></li>
            </ol>
            <h3 className="sub">{t('usesTitle')}</h3>
            <div className="notes uses">
              <div><h3>{t('use1')}</h3><p>{t('use1Text')}</p></div>
              <div><h3>{t('use2')}</h3><p>{t('use2Text')}</p></div>
              <div><h3>{t('use3')}</h3><p>{t('use3Text')}</p></div>
              <div><h3>{t('use4')}</h3><p>{t('use4Text')}</p></div>
            </div>
          </div>
        </section>

        {/* 07 — forecast horizon: accuracy against lead time, from the ledger */}
        <section className="slide" id="horizon" aria-labelledby="horizon-h">
          <div className="wrap">
            <Counter n={7} t={t} />
            <h2 id="horizon-h">{t('hzTitle')}</h2>
            <p className="intro">{t('hzIntro')}</p>
            <HorizonTable t={t} num={num} />
            <p className="meta">{t('hzPending')}</p>
            <div className="honest">
              <h3>{t('hzReadTitle')}</h3>
              <p>{t('hzRead')}</p>
            </div>
            <p className="meta hz-meta">{t('hzMeta')}</p>
          </div>
        </section>

        {/* 08 — competition */}
        <section className="slide" id="competition" aria-labelledby="cmp-h">
          <div className="wrap">
            <Counter n={8} t={t} />
            <h2 id="cmp-h">{t('cmpTitle')}</h2>
            <p className="intro">{t('cmpIntro')}</p>
            <div className="table-scroll">
              <table className="matrix">
                <caption className="sr-only">{t('cmpTitle')}</caption>
                <thead>
                  <tr>
                    <td />
                    {CMP_COLS.map((c, i) => <th key={c} scope="col" className={i === 0 ? 'us' : undefined}>{t(c)}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {CMP_ROWS.map(([row, cells]) => (
                    <tr key={row}>
                      <th scope="row">{t(row)}</th>
                      {cells.map((c, i) => (
                        <td key={i} className={[i === 0 ? 'us' : '', c === 'cmpYes' ? 'yes' : '', c === 'cmpNo' ? 'no' : ''].join(' ').trim() || undefined}>
                          {t(c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="meta cmp-note">{t('cmpNote')}</p>
          </div>
        </section>

        {/* 09 — market */}
        <section className="slide" id="market" aria-labelledby="mkt-h">
          <div className="wrap">
            <Counter n={9} t={t} />
            <h2 id="mkt-h">{t('mktTitle')}</h2>
            <p className="intro">{t('mktIntro')}</p>
            <dl className="market">
              {MARKET.map(k => (
                <div key={k} className={`mkt ${k}`}>
                  <dt>
                    <span className="mkt-label">{t(`${k}Label`)}</span>
                    <span className="mkt-fig">{t(`${k}Fig`)}</span>
                    <span className="mkt-what">{t(`${k}What`)}</span>
                  </dt>
                  <dd>{t(`${k}Text`)}</dd>
                </div>
              ))}
            </dl>
            <p className="reading">{t('mktPlayers')}</p>
            <p className="meta">{t('mktMeta')}</p>
          </div>
        </section>

        {/* 10 — gain */}
        <section className="slide" id="gain" aria-labelledby="gain-h">
          <div className="wrap">
            <Counter n={10} t={t} />
            <h2 id="gain-h">{t('gainTitle')}</h2>
            <p className="intro">{t('gainIntro')}</p>
            <GainCalculator t={t} num={num} />
            <div className="honest">
              <h3>{t('gainHonestTitle')}</h3>
              <p>{t('gainHonest')}</p>
            </div>
          </div>
        </section>

        {/* 11 — why us */}
        <section className="slide" aria-labelledby="why-h">
          <div className="wrap">
            <Counter n={11} t={t} />
            <h2 id="why-h">{t('whyTitle')}</h2>
            <div className="notes why">
              <div><h3>{t('why1')}</h3><p>{t('why1Text')}</p></div>
              <div><h3>{t('why2')}</h3><p>{t('why2Text')}</p></div>
              <div><h3>{t('why3')}</h3><p>{t('why3Text')}</p></div>
              <div><h3>{t('why4')}</h3><p>{t('why4Text')}</p></div>
            </div>
          </div>
        </section>

        {/* 12 — roadmap and ask */}
        <section className="slide ask" id="roadmap" aria-labelledby="road-h">
          <div className="wrap">
            <Counter n={12} t={t} />
            <h2 id="road-h">{t('roadTitle')}</h2>
            <ol className="milestones">
              {[0, 1, 2, 3, 4, 5].map(i => (
                <li key={i} className={i === 0 ? 'done' : undefined}>
                  <span className="ms-id">M{i}</span>
                  <span className="ms-when">{t(`m${i}When`)}{i === 0 && <> · {t('roadDone')}</>}</span>
                  <h3>{t(`m${i}`)}</h3>
                  <p>{t(`m${i}Text`)}</p>
                </li>
              ))}
            </ol>
            <div className="ask-box">
              <h2 id="ask-h">{t('askTitle')}</h2>
              <p className="intro">{t('askText')}</p>
              <div className="actions">
                <a className="btn primary" href={MAP_URL}>{t('askCta')}</a>
                <a className="btn quiet" href={TRAINING_URL}>{t('technical')}</a>
              </div>
            </div>
          </div>
        </section>

        {/* 13 — references */}
        <section className="slide refs" id="references" aria-labelledby="ref-h">
          <div className="wrap">
            <Counter n={13} t={t} />
            <h2 id="ref-h">{t('refTitle')}</h2>
            <p className="intro">{t('refIntro')}</p>
            <ol className="ref-list">
              {REFS.map(r => (
                <li key={r.href + r.cite.slice(0, 20)}>
                  {r.cite} <a href={r.href} rel="noopener">↗</a>
                </li>
              ))}
            </ol>
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

/** Ledger results per horizon; a horizon with no result says so instead of a number. */
function HorizonTable({ t, num }) {
  const label = m => (m < 60 ? t('hzMin', { n: m }) : t('hzHour', { n: num(m / 60, 0) }));
  const auc = ([v, lo, hi]) => `${num(v, 2)} [${num(lo, 2)}–${num(hi, 2)}]`;
  const pct = ([v, lo, hi]) => `${t('pct', { n: Math.round(v * 100) })} [${Math.round(lo * 100)}–${Math.round(hi * 100)}]`;
  return (
    <div className="table-scroll">
      <table className="sources horizon">
        <caption className="sr-only">{t('hzTitle')}</caption>
        <thead>
          <tr>
            <th scope="col">{t('hzColHorizon')}</th>
            <th scope="col">{t('hzColPooled')}</th>
            <th scope="col">{t('hzColHit')}</th>
            <th scope="col">{t('hzColWithin')}</th>
            <th scope="col">{t('hzColN')}</th>
          </tr>
        </thead>
        <tbody>
          {HORIZONS.map(h => (
            <tr key={h.min} className={h.pending ? 'pending' : undefined}>
              <th scope="row">{label(h.min)}</th>
              {h.pending ? (
                <td colSpan={4}>{t('hzPendingRow')}</td>
              ) : (
                <>
                  <td className="num em">{auc(h.pooled)}</td>
                  <td className={`num${h.hit[1] > 0.2 ? ' above' : h.hit[2] < 0.2 ? ' below' : ''}`}>{pct(h.hit)}</td>
                  <td className="num">{auc(h.within)}</td>
                  <td className="num">{num(h.n, 0)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

/** 1 / (1 - f): arithmetic on a stated assumption, not a measurement. Flight
 * duration, never range — see the wording rules in strings.js. */
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
          <span className="lift">{t('gainPct', { n: Math.round((mult - 1) * 100) })}</span>
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
