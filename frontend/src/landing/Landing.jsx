import { useEffect, useState, useCallback } from 'react';
import { useLang }  from '../i18n/LanguageContext.jsx';
import { useTheme } from '../theme/ThemeContext.jsx';
import { LANG_LABEL } from '../i18n/strings.js';
import { API_BASE } from '../config.js';
import { translateLanding } from './strings.js';
import ThermalField  from './ThermalField.jsx';
import Barogram      from './Barogram.jsx';
import IntervalTable from './IntervalTable.jsx';
import { BrandMark, SunIcon, MoonIcon } from '../components/Icons.jsx';

// The map app lives at the site root; these are views of it people link to
// (see the ?compare and ?panel handling in App.jsx).
const MAP_URL      = '/';
const COMPARE_URL  = '/?compare=1';
const TRAINING_URL = '/?panel=training';
// The pre-seed pitch for defence partners, a third page of the same build.
const PITCH_URL    = '/sunum';

// Within-(day, cell, hour) ranking score, micro AUC with 95% bootstrap CI.
// `python -m evaluation.evaluate --variant base --by-hour`, run 16 Nov 2026 on
// the 6 Nov 2026 buffer minus its invented-weather rows (47,419 rows, 6,259
// positives, 2,009 mixed-label units). "xgb base (served)" is rowModel;
// physics_fallback is rowPhysics. Ordered worst to best so the model reads as
// the bottom line. Re-run and replace these together with the copy in
// strings.js (scoreMeta, scoreRead, regionNote, turkiyeText).
const SCORES = [
  { key: 'rowAlt',     value: 0.501, lo: 0.474, hi: 0.529 },
  { key: 'rowPhysics', value: 0.502, lo: 0.490, hi: 0.513 },
  { key: 'rowTerrain', value: 0.525, lo: 0.493, hi: 0.557 },
  { key: 'rowSun',     value: 0.551, lo: 0.520, hi: 0.580 },
  { key: 'rowModel',   value: 0.570, lo: 0.539, hi: 0.601, emphasis: true },
];

// Same run, served model by region (evaluation/regions.py). Türkiye is left out
// of the table because it is not measurable — 1 mixed-label group of the 30
// needed — and turkiyeText says so in words instead.
const REGIONS = [
  { key: 'regAlps',    value: 0.612, lo: 0.569, hi: 0.653 },
  { key: 'regIberia',  value: 0.611, lo: 0.530, hi: 0.685 },
  { key: 'regGermany', value: 0.575, lo: 0.528, hi: 0.628 },
  { key: 'regOther',   value: 0.523, lo: 0.493, hi: 0.558 },
];

/**
 * How many thermals the feed is marking right now — the one live number on the
 * page. null means "do not show the line": the request failed, timed out, or
 * there is no backend behind this build. A landing page that explains the
 * project must never look broken because the API is busy.
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

  useEffect(() => { document.title = t('docTitle'); }, [t]);

  const langLabel  = lang === 'tr' ? tApp('switchToEnglish') : tApp('switchToTurkish');
  const themeLabel = theme === 'dark' ? tApp('switchToLight') : tApp('switchToDark');

  return (
    <>
      <a className="skip" href="#main">{t('skipToContent')}</a>

      <header className="hero">
        <div className="wrap nav">
          <a className="brand" href="#top" id="top"><BrandMark className="brand-mark" size={22} />ThermalSense</a>
          <nav className="nav-links" aria-label={t('navLabel')}>
            <a href="#see">{t('navSee')}</a>
            <a href="#learn">{t('navLearn')}</a>
            <a href="#score">{t('navScore')}</a>
            <a href="#data">{t('navData')}</a>
            <a href={PITCH_URL}>{t('navPitch')}</a>
          </nav>
          <div className="nav-tools">
            <button type="button" className="tool" onClick={toggleLang} title={langLabel} aria-label={langLabel}>
              {LANG_LABEL[lang]}
            </button>
            {/* Shows the mode the click leads to, as the app's own toggle does. */}
            <button type="button" className="tool" onClick={toggleTheme} title={themeLabel} aria-label={themeLabel}>
              {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
            </button>
            <a className="btn primary small" href={MAP_URL}>{t('openMapShort')}</a>
          </div>
        </div>

        <div className="wrap hero-body">
          <h1 className="hero-title">
            <span className="unseen">{t('heroUnseen')}</span>{' '}
            <span className="seen">{t('heroSeen')}</span>
          </h1>
          <p className="lede">{t('heroLede')}</p>
          <div className="actions">
            <a className="btn primary" href={MAP_URL}>{t('openMap')}</a>
            <a className="btn quiet" href="#learn">{t('heroSecondary')}</a>
          </div>
        </div>

        <div className="wrap">
          <figure className="field-figure">
            <ThermalField t={t} />
            <figcaption>{t('simCaption')}</figcaption>
          </figure>
          {live != null && (
            <p className="live">
              <span className={`live-dot${live > 0 ? ' on' : ''}`} aria-hidden="true" />
              {live > 0 ? t('liveNow', { n: live }) : t('liveNone')}
            </p>
          )}
        </div>
      </header>

      <main id="main">
        <section className="band" id="see" aria-labelledby="see-h">
          <div className="wrap">
            <h2 id="see-h">{t('seeTitle')}</h2>
            <dl className="facts">
              <Fact figure="201 × 201" unit={t('seeCellsUnit')}>{t('seeCells')}</Fact>
              <Fact figure="50" unit={t('seePassesUnit')}>{t('seePasses')}</Fact>
              <Fact figure={t('seeAheadFig')} unit={t('seeAheadUnit')}>{t('seeAhead')}</Fact>
              <Fact figure={t('seeLiveFig')} unit={t('seeLiveUnit')}>{t('seeLive')}</Fact>
            </dl>
            <div className="notes">
              <div>
                <h3>{t('noteAltTitle')}</h3>
                <p>{t('noteAlt')}</p>
              </div>
              <div>
                <h3>{t('noteNightTitle')}</h3>
                <p>{t('noteNight')}</p>
              </div>
              <div>
                <h3>{t('noteRawTitle')}</h3>
                <p>{t('noteRaw')} <a href={COMPARE_URL}>{t('noteRawLink')}</a></p>
              </div>
            </div>
          </div>
        </section>

        <section className="band" id="learn" aria-labelledby="learn-h">
          <div className="wrap">
            <h2 id="learn-h">{t('learnTitle')}</h2>
            <p className="intro">{t('learnIntro')}</p>

            <figure className="baro-figure">
              <Barogram t={t} />
              <figcaption>
                <ul className="labels">
                  <li>
                    <span className="swatch pos" aria-hidden="true" />
                    <strong>{t('labelPos')}</strong>
                    <span>{t('labelPosRule')}</span>
                  </li>
                  <li>
                    <span className="swatch neg" aria-hidden="true" />
                    <strong>{t('labelNeg')}</strong>
                    <span>{t('labelNegRule')}</span>
                  </li>
                  <li>
                    <span className="swatch drop" aria-hidden="true" />
                    <strong>{t('labelDrop')}</strong>
                    <span>{t('labelDropRule')}</span>
                  </li>
                </ul>
              </figcaption>
            </figure>

            <div className="split">
              <div>
                <h3>{t('whyDropTitle')}</h3>
                <p>{t('whyDrop')}</p>
              </div>
              <div>
                <h3>{t('stepsTitle')}</h3>
                {/* Numbered because it is a pipeline: each step only ever sees
                    what the one before it let through. */}
                <ol className="steps">
                  <li><strong>{t('stepFeed')}</strong><span>{t('stepFeedText')}</span></li>
                  <li><strong>{t('stepGate')}</strong><span>{t('stepGateText')}</span></li>
                  <li><strong>{t('stepLabel')}</strong><span>{t('stepLabelText')}</span></li>
                  <li><strong>{t('stepFit')}</strong><span>{t('stepFitText')}</span></li>
                  <li><strong>{t('stepQuality')}</strong><span>{t('stepQualityText')}</span></li>
                </ol>
              </div>
            </div>
          </div>
        </section>

        <section className="band" id="score" aria-labelledby="score-h">
          <div className="wrap">
            <h2 id="score-h">{t('scoreTitle')}</h2>
            <p className="verdict">{t('scoreLead')}</p>
            <p className="intro">{t('scoreIntro')}</p>

            <IntervalTable
              caption={t('scoreCaption')}
              columns={{ label: t('colRanker'), value: t('colScore'), interval: t('colCi') }}
              rows={SCORES.map(r => ({ ...r, label: t(r.key) }))}
              domain={[0.46, 0.70]}
              reference={0.5}
              referenceLabel={t('chance')}
              ticks={[0.5, 0.6, 0.7]}
              locale={locale}
            />
            <p className="meta">{t('scoreMeta')}</p>
            <p className="reading">{t('scoreRead')}</p>

            {/* Same scale as the table above, so the two read as one chart. */}
            <h3 className="sub">{t('regionTitle')}</h3>
            <IntervalTable
              caption={t('regionCaption')}
              columns={{ label: t('colRegion'), value: t('colScore'), interval: t('colCi') }}
              rows={REGIONS.map(r => ({ ...r, label: t(r.key) }))}
              domain={[0.46, 0.70]}
              reference={0.5}
              referenceLabel={t('chance')}
              ticks={[0.5, 0.6, 0.7]}
              locale={locale}
            />
            <p className="meta">{t('regionNote')}</p>

            <div className="notes three">
              <div>
                <h3>{t('nextTitle')}</h3>
                <p>{t('nextText')}</p>
              </div>
              <div>
                <h3>{t('useTitle')}</h3>
                <p>{t('useText')}</p>
              </div>
              <div>
                <h3>{t('turkiyeTitle')}</h3>
                <p>{t('turkiyeText')} <a href={TRAINING_URL}>{t('turkiyeLink')}</a></p>
              </div>
            </div>
          </div>
        </section>

        <section className="band" id="data" aria-labelledby="data-h">
          <div className="wrap">
            <h2 id="data-h">{t('dataTitle')}</h2>
            <div className="split table-first">
              <table className="sources">
                <caption className="sr-only">{t('dataCaption')}</caption>
                <thead>
                  <tr><th scope="col">{t('colSource')}</th><th scope="col">{t('colUse')}</th></tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row"><a href="https://www.glidernet.org/" rel="noopener">Open Glider Network</a></th>
                    <td>{t('srcOgnUse')}</td>
                  </tr>
                  <tr>
                    <th scope="row"><a href="https://open-meteo.com/" rel="noopener">Open-Meteo</a></th>
                    <td>{t('srcMeteoUse')}</td>
                  </tr>
                  <tr>
                    <th scope="row"><a href="https://registry.opendata.aws/copernicus-dem/" rel="noopener">Copernicus GLO-30</a></th>
                    <td>{t('srcDemUse')}</td>
                  </tr>
                </tbody>
              </table>
              <div>
                <h3>{t('machineTitle')}</h3>
                <p>{t('machineText')}</p>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="close">
        <div className="wrap">
          <p className="close-title">{t('closeTitle')}</p>
          <a className="btn primary" href={MAP_URL}>{t('openMap')}</a>
          <p className="disclaimer">{t('disclaimer')}</p>
          <p className="credits">
            {t('creditTerrain')} {t('creditWeather')} {t('creditTraffic')}
          </p>
        </div>
      </footer>
    </>
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
