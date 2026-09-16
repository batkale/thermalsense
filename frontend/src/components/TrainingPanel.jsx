import { useEffect, useState } from 'react';
import { fetchTrainingStatus } from '../services/api.js';
import { useLang } from '../i18n/LanguageContext.jsx';

// Polled only while open. Everything on it moves on the scale of the retrain
// cycle (300 s) or slower, so 30 s is already generous.
const POLL_MS = 30_000;

// Region keys arrive from the backend as data names; the labels are translated.
const REGION_KEYS = {
  'Türkiye': 'regionTurkiye',
  Alps: 'regionAlps',
  Germany: 'regionGermany',
  Iberia: 'regionIberia',
  other: 'regionOther',
};

function ago(iso, t) {
  if (!iso) return null;
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 90) return t('agoSeconds', { n: s });
  if (s < 5400) return t('agoMinutes', { n: Math.round(s / 60) });
  return t('agoHours', { n: Math.round(s / 3600) });
}

function Bar({ value, max, warnAt }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  const cls = warnAt != null && value / max >= warnAt ? 'tp-bar-fill warn' : 'tp-bar-fill';
  return (
    <div className="tp-bar" role="progressbar" aria-valuenow={value} aria-valuemax={max}>
      <div className={cls} style={{ width: `${pct}%` }} />
    </div>
  );
}

function Row({ label, value, tone }) {
  return (
    <div className="tp-row">
      <span className="tp-label">{label}</span>
      <span className={`tp-value${tone ? ` ${tone}` : ''}`}>{value}</span>
    </div>
  );
}

function Section({ title, error, children }) {
  const { t } = useLang();
  return (
    <section className="tp-section">
      <h3>{title}</h3>
      {error ? <div className="tp-error">{t('sectionUnavailable', { e: error })}</div> : children}
    </section>
  );
}

export default function TrainingPanel({ onClose }) {
  const { t, locale } = useLang();
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => fetchTrainingStatus()
      .then(d => { if (alive) { setData(d); setFailed(false); } })
      .catch(() => { if (alive) setFailed(true); });
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const n = v => (v == null ? '—' : Number(v).toLocaleString(locale));

  let body;
  if (!data) {
    body = <div className="tp-hint">{failed ? t('trainingStatusFailed') : t('loadingStatus')}</div>;
  } else {
    const { feed, model, buffer, archive, climatology, terrain_api: api } = data;
    const fit = model.last_fit;
    const groups = buffer.benchmark_groups ?? 0;
    const need = buffer.benchmark_groups_needed ?? 30;
    const turkey = buffer.regions?.['Türkiye'];

    body = (
      <>
        {/* The one question the panel exists to answer, first. */}
        <div className={`tp-verdict ${groups >= need ? 'ready' : 'waiting'}`}>
          {groups >= need
            ? t('gateReady', { g: groups, need })
            : t('gateWaiting', { g: groups, need })}
          <Bar value={groups} max={need} />
        </div>

        {turkey && turkey.rows === 0 && (
          <div className="tp-callout">{t('turkiyeNoData')}</div>
        )}

        <Section title={t('sectionFeed')} error={feed.beacons?.error}>
          <Row label={t('feedConnection')}
               value={feed.connected ? t('connected') : t('disconnected')}
               tone={feed.connected ? 'ok' : 'bad'} />
          <Row label={t('feedArea')} value={feed.filter === 'worldwide' ? t('worldwide') : feed.filter} />
          <Row label={t('glidersInView')} value={n(feed.gliders_in_view)} />
          <Row label={t('beaconsLastHour')} value={n(feed.beacons?.last_hour)} />
        </Section>

        <Section title={t('sectionModel')}>
          <Row label={t('lastFit')}
               value={fit ? `${t(`fit_${fit.outcome}`)} · ${ago(fit.at, t)}` : t('noFitYet')}
               tone={fit && ['accepted', 'rebaselined'].includes(fit.outcome) ? 'ok' : undefined} />
          {fit?.delta && (
            <Row label={t('pairedDelta')}
                 value={`${fit.delta[1] >= 0 ? '+' : ''}${fit.delta[1].toFixed(3)} [${fit.delta[0].toFixed(3)}, ${fit.delta[2].toFixed(3)}]`} />
          )}
          <Row label={t('benchmarkScore')}
               value={model.benchmark_score == null ? t('notComparable') : model.benchmark_score.toFixed(3)} />
        </Section>

        <Section title={t('sectionBuffer')} error={buffer.error}>
          <Row label={t('samples')} value={t('samplesValue', { n: n(buffer.rows), p: n(buffer.positives) })} />
          <Row label={t('distinctDays')} value={n(buffer.days)} />
          <table className="tp-regions">
            <thead>
              <tr><th>{t('region')}</th><th>{t('samples')}</th><th>{t('positivesShort')}</th></tr>
            </thead>
            <tbody>
              {Object.entries(buffer.regions ?? {}).map(([name, r]) => (
                <tr key={name} className={name === 'Türkiye' ? 'home' : undefined}>
                  <td>{t(REGION_KEYS[name] ?? 'regionOther')}</td>
                  <td>{n(r.rows)}</td>
                  <td>{n(r.positives)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title={t('sectionStorage')} error={archive.error}>
          <Row label={t('archiveDays')}
               value={archive.days ? t('archiveValue', { d: n(archive.days), mb: n(Math.round(archive.bytes / 1e6)) }) : t('archiveEmpty')} />
          <Row label={t('climatologyBuckets')} value={n(climatology.buckets)} />
          <Row label={t('diskFree')} value={`${n(data.disk_free_gb)} GB`}
               tone={data.disk_free_gb < 3 ? 'bad' : undefined} />
        </Section>

        <Section title={t('sectionTerrainApi')}>
          <Row label={t('requestsToday')} value={`${n(api.requests)} / ${n(api.quota)}`}
               tone={api.requests >= api.quota ? 'bad' : api.requests >= 0.8 * api.quota ? 'warn' : undefined} />
          <Bar value={api.requests} max={api.quota} warnAt={0.8} />
          {api.rate_limited > 0 && <div className="tp-hint">{t('rateLimited', { n: n(api.rate_limited) })}</div>}
        </Section>
      </>
    );
  }

  return (
    <div className="tp-overlay" onClick={onClose}>
      <div className="tp-panel" role="dialog" aria-label={t('trainingStatus')} onClick={e => e.stopPropagation()}>
        <header className="tp-header">
          <h2>{t('trainingStatus')}</h2>
          <button className="tp-close" onClick={onClose} aria-label={t('close')}>×</button>
        </header>
        {body}
      </div>
    </div>
  );
}
