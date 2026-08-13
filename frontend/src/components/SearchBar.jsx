import { useState, useEffect, useRef, useCallback } from 'react';
import { geocodeQuery } from '../services/search.js';
import { useLang } from '../i18n/LanguageContext.jsx';

const TAG_KEY = { glider: 'tagGlider', airport: 'tagAirport', place: 'tagPlace' };

export default function SearchBar({ gliders, onSelect }) {
  const { t, lang } = useLang();
  const [query,   setQuery]   = useState('');
  const [results, setResults] = useState([]);
  const [open,    setOpen]    = useState(false);
  const [active,  setActive]  = useState(-1);
  const inputRef = useRef(null);
  const timerRef = useRef(null);

  const runSearch = useCallback(async (q) => {
    const lq = q.trim().toLowerCase();
    if (!lq) { setResults([]); setOpen(false); return; }

    const gliderHits = gliders
      .filter(g => g.id.toLowerCase().includes(lq))
      .slice(0, 3)
      .map(g => ({
        type:     'glider',
        label:    g.id,
        sublabel: t('searchAltVario', {
          alt:   g.alt,
          vario: `${g.vario >= 0 ? '+' : ''}${g.vario}`,
        }),
        lat:  g.lat,
        lon:  g.lon,
        zoom: 13,
      }));

    setResults(gliderHits);
    setOpen(true);

    try {
      const places = await geocodeQuery(q, lang);
      setResults([...gliderHits, ...places]);
    } catch { /* network offline — glider results only */ }
  }, [gliders, t, lang]);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => runSearch(query), 300);
    return () => clearTimeout(timerRef.current);
  }, [query, runSearch]);

  function commit(item) {
    setQuery(item.label);
    setOpen(false);
    setActive(-1);
    onSelect({ lat: item.lat, lon: item.lon, zoom: item.zoom ?? 12 });
  }

  function handleKey(e) {
    if (!open || !results.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(a + 1, results.length - 1)); }
    if (e.key === 'ArrowUp')   { e.preventDefault(); setActive(a => Math.max(a - 1, 0)); }
    if (e.key === 'Enter' && active >= 0) commit(results[active]);
    if (e.key === 'Escape') { setOpen(false); inputRef.current?.blur(); }
  }

  function clear() {
    setQuery('');
    setResults([]);
    setOpen(false);
    inputRef.current?.focus();
  }

  return (
    <div className="search-wrap">
      <div className="search-box">
        <input
          ref={inputRef}
          className="search-input"
          placeholder={t('searchPlaceholder')}
          value={query}
          onChange={e => { setQuery(e.target.value); setOpen(true); setActive(-1); }}
          onFocus={() => query.trim() && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={handleKey}
          autoComplete="off"
          spellCheck={false}
        />
        {query && (
          <button className="search-clear" onClick={clear} tabIndex={-1} title={t('clearSearch')}>
            &times;
          </button>
        )}
      </div>

      {open && results.length > 0 && (
        <ul className="search-dropdown">
          {results.map((r, i) => (
            <li
              key={i}
              className={`search-item${i === active ? ' active' : ''}`}
              onMouseDown={() => commit(r)}
              onMouseEnter={() => setActive(i)}
            >
              <span className={`search-tag tag-${r.type}`}>{t(TAG_KEY[r.type])}</span>
              <div className="search-text">
                <span className="search-label">{r.label}</span>
                {r.sublabel && <span className="search-sub">{r.sublabel}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
