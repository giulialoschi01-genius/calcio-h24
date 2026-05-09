#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update.py — Calcio H24
Aggiornamento giornaliero: classifiche + notizie RSS + notizie redazione.
Le API Key vengono lette dalle variabili d'ambiente GitHub Secrets.

Versione corretta del 09/05/2026:
- FIX: auto-detect stagione corrente del calcio europeo + fallback su stagioni precedenti
       (necessario perché il piano Free di API-Football limita le stagioni disponibili).
- FIX: logging diagnostico delle risposte API (campi 'errors' e 'results').
- FIX: chiamata iniziale a /status per loggare piano e quota residua.
- FIX: URL RSS aggiornato (i 4 vecchi feed erano tutti morti). Ridotto a 1 fonte
       verificata (Corriere dello Sport — Calcio).
- FIX: parse_data_rss ora normalizza sempre datetime aware (evita TypeError
       in ordinamento quando una data ISO arriva senza timezone).
"""

import os
import json
import re
import glob
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import feedparser
import frontmatter

# ─── CONFIGURAZIONE ─────────────────────────────────────────────────────────────

API_FOOTBALL_KEY = os.environ.get('API_FOOTBALL_KEY', '')

LEAGUE_SERIE_A   = 135
LEAGUE_CHAMPIONS = 2

# Numero di stagioni di fallback da provare se la corrente è vuota (piano Free).
STAGIONI_FALLBACK = 3   # prova: corrente, corrente-1, corrente-2

# Percorsi relativi alla root del repository (dove viene eseguito lo script)
BASE_DIR                 = os.path.dirname(os.path.abspath(__file__))
PERCORSO_CLASSIFICHE     = os.path.join(BASE_DIR, 'data', 'classifiche.json')
PERCORSO_NOTIZIE_RSS     = os.path.join(BASE_DIR, 'data', 'notizie_rss.json')
PERCORSO_NOTIZIE_RED     = os.path.join(BASE_DIR, 'data', 'notizie_redazione.json')
PERCORSO_CONTENT_NOTIZIE = os.path.join(BASE_DIR, 'content', 'notizie')

# Solo Corriere dello Sport — Calcio (URL ufficiale verificato 09/05/2026).
# Per aggiungere altri feed CdS in futuro basta scommentare le righe sotto.
RSS_FEEDS = {
    'Corriere dello Sport — Calcio': 'https://www.corrieredellosport.it/rss/calcio',
    # 'Corriere dello Sport — Primo Piano': 'https://www.corrieredellosport.it/rss/',
    # 'Corriere dello Sport — Serie A':     'https://www.corrieredellosport.it/rss/calcio/serie-a',
    # 'Corriere dello Sport — Mercato':     'https://www.corrieredellosport.it/rss/calcio/calcio-mercato',
}

MAX_PER_FEED = 10        # con 1 sola fonte alziamo per avere più varietà
RSS_TIMEOUT  = 12        # secondi
API_TIMEOUT  = 20        # secondi
USER_AGENT   = 'Mozilla/5.0 (compatible; CalcioH24Bot/1.0)'


# ─── HTTP SESSION CON RETRY ─────────────────────────────────────────────────────

def crea_sessione() -> requests.Session:
    """Sessione HTTP con retry automatico su errori transitori."""
    sessione = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,          # attesa: 1s, 2s, 4s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    sessione.mount('https://', adapter)
    sessione.mount('http://', adapter)
    sessione.headers.update({'User-Agent': USER_AGENT})
    return sessione


SESSION = crea_sessione()


# ─── STAGIONE CORRENTE (auto-detect) ────────────────────────────────────────────

def stagione_corrente_calcio() -> int:
    """
    API-Football usa l'anno di INIZIO stagione (es. 2025 = stagione 2025/26).
    Per il calcio europeo la stagione cambia tipicamente a luglio.
    """
    oggi = datetime.now(timezone.utc)
    return oggi.year if oggi.month >= 7 else oggi.year - 1


# ─── DIAGNOSTICA API-FOOTBALL ───────────────────────────────────────────────────

def log_status_api() -> None:
    """
    Chiamata diagnostica iniziale a /status: stampa piano e quota residua.
    Utile per capire al volo se errori successivi sono dovuti a quota o piano.
    """
    if not API_FOOTBALL_KEY:
        print('  ⚠ API_FOOTBALL_KEY non impostata — skip diagnostica /status')
        return
    try:
        r = SESSION.get(
            'https://v3.football.api-sports.io/status',
            headers={'x-apisports-key': API_FOOTBALL_KEY},
            timeout=API_TIMEOUT,
        )
        r.raise_for_status()
        dati = r.json()
        risp = dati.get('response', {}) or {}
        plan = (risp.get('subscription', {}) or {}).get('plan', '?')
        active = (risp.get('subscription', {}) or {}).get('active', '?')
        req = risp.get('requests', {}) or {}
        current = req.get('current', '?')
        limit = req.get('limit_day', '?')
        print(f'  ℹ Piano API-Football: {plan} (attivo: {active}) — richieste: {current}/{limit}')
        errs = dati.get('errors')
        if errs:
            print(f'  ⚠ /status errors: {errs}')
    except Exception as e:
        print(f'  ✗ Errore diagnostica /status: {e}')


# ─── CLASSIFICHE ────────────────────────────────────────────────────────────────

def scarica_classifica(league_id: int, stagione: int) -> list:
    """Singolo tentativo: ritorna lista normalizzata o [] in caso di problema."""
    if not API_FOOTBALL_KEY:
        print('  ⚠ API_FOOTBALL_KEY non impostata — skip classifica')
        return []

    url     = 'https://v3.football.api-sports.io/standings'
    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    params  = {'league': league_id, 'season': stagione}

    try:
        risposta = SESSION.get(url, headers=headers, params=params, timeout=API_TIMEOUT)
        risposta.raise_for_status()
        dati = risposta.json()

        # Diagnostica: l'API mette gli errori "logici" in un campo dedicato.
        errs = dati.get('errors')
        if errs:
            # Può essere lista o dict, dipende dal tipo di errore.
            print(f'  ⚠ API errors (league {league_id}, season {stagione}): {errs}')

        # Se response è vuoto, la combinazione league/season non è disponibile
        # (tipico sul piano Free per le stagioni recenti).
        response = dati.get('response') or []
        if not response:
            results = dati.get('results', 0)
            print(f'  ✗ Response vuota (league {league_id}, season {stagione}, results={results})')
            return []

        standings_raw = response[0].get('league', {}).get('standings', [])
        if not standings_raw:
            print(f'  ✗ Standings vuota (league {league_id}, season {stagione})')
            return []

        # UCL formato svizzero 2024/25: standings_raw può essere lista di liste
        # (gruppi) o lista piatta di squadre. Appiattimento robusto.
        squadre = []
        for elemento in standings_raw:
            if isinstance(elemento, list):
                squadre.extend(elemento)
            elif isinstance(elemento, dict):
                squadre.append(elemento)

        return [normalizza_squadra(s) for s in squadre]

    except (KeyError, IndexError, TypeError) as e:
        print(f'  ✗ Struttura risposta imprevista (league {league_id}, season {stagione}): {e}')
        return []
    except requests.RequestException as e:
        print(f'  ✗ Errore HTTP (league {league_id}, season {stagione}): {e}')
        return []


def scarica_classifica_con_fallback(league_id: int, stagione_iniziale: int) -> tuple:
    """
    Prova la stagione corrente e, se vuota, fa fallback alle precedenti.
    Ritorna (stagione_usata, classifica) — stagione_usata è None se nessuna ha dati.
    """
    for offset in range(STAGIONI_FALLBACK):
        s = stagione_iniziale - offset
        if offset == 0:
            print(f'  → league {league_id}: provo stagione {s}/{s+1}')
        else:
            print(f'  → league {league_id}: fallback stagione {s}/{s+1}')
        classifica = scarica_classifica(league_id, s)
        if classifica:
            print(f'  ✓ league {league_id}: classifica ottenuta per stagione {s}/{s+1} ({len(classifica)} squadre)')
            return s, classifica
    print(f'  ✗ league {league_id}: nessuna stagione ha restituito dati (provate {STAGIONI_FALLBACK})')
    return None, []


def normalizza_squadra(entry: dict) -> dict:
    team  = entry.get('team', {})
    stats = entry.get('all', {})
    gol   = stats.get('goals', {})
    return {
        'posizione':        entry.get('rank', 0),
        'squadra':          team.get('name', ''),
        'logo':             team.get('logo', ''),
        'giocate':          stats.get('played', 0),
        'vinte':            stats.get('win', 0),
        'pareggiate':       stats.get('draw', 0),
        'perse':            stats.get('lose', 0),
        'gol_fatti':        gol.get('for', 0),
        'gol_subiti':       gol.get('against', 0),
        'differenza_reti':  entry.get('goalsDiff', 0),
        'punti':            entry.get('points', 0),
        'forma':            entry.get('form', ''),
        'descrizione':      entry.get('description', ''),
    }


# ─── RSS ────────────────────────────────────────────────────────────────────────

def estrai_immagine_entry(entry) -> str:
    """Cerca immagine anteprima nei campi RSS più comuni."""
    try:
        if entry.media_thumbnail:
            return entry.media_thumbnail[0].get('url', '')
    except AttributeError:
        pass
    try:
        if entry.media_content:
            url = entry.media_content[0].get('url', '')
            if url:
                return url
    except AttributeError:
        pass
    try:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href', '')
    except AttributeError:
        pass
    sommario = getattr(entry, 'summary', '') or ''
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', sommario)
    if match:
        url = match.group(1)
        if not any(x in url for x in ['1x1', 'pixel', 'tracking', 'spacer']):
            return url
    return ''


def parse_data_rss(data_raw: str) -> datetime:
    """
    Converte una stringa data RSS (RFC 2822 o ISO) in datetime SEMPRE aware (UTC).
    Ritorna datetime.min UTC in caso di errore (la notizia va in fondo).
    """
    fallback = datetime.min.replace(tzinfo=timezone.utc)
    if not data_raw:
        return fallback
    # Tentativo 1: formato RFC 2822 (tipico RSS)
    try:
        dt = parsedate_to_datetime(data_raw)
        if dt is not None:
            # Se naive, assumiamo UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception:
        pass
    # Tentativo 2: ISO 8601
    try:
        dt = datetime.fromisoformat(data_raw.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return fallback


def scarica_feed_rss(nome: str, url: str) -> list:
    """
    Scarica e analizza un feed RSS.
    Usa requests con timeout esplicito prima di passare il contenuto a feedparser.
    """
    notizie = []
    try:
        risposta = SESSION.get(url, timeout=RSS_TIMEOUT)
        risposta.raise_for_status()
        feed = feedparser.parse(risposta.content)

        if not feed.entries:
            print(f'  ✗ {nome}: feed vuoto o irraggiungibile')
            return []

        for entry in feed.entries[:MAX_PER_FEED]:
            titolo = (entry.get('title', '') or '').strip()
            if not titolo:
                continue
            data_raw = entry.get('published', entry.get('updated', ''))
            notizie.append({
                'fonte':    nome,
                'titolo':   titolo,
                'link':     entry.get('link', ''),
                'data':     data_raw,
                '_dt':      parse_data_rss(data_raw),   # campo interno per ordinamento
                'immagine': estrai_immagine_entry(entry),
            })

        print(f'  ✓ {nome}: {len(notizie)} notizie')

    except requests.RequestException as e:
        print(f'  ✗ Errore RSS {nome}: {e}')
    except Exception as e:
        print(f'  ✗ Errore inatteso RSS {nome}: {e}')

    return notizie


def normalizza_url(url: str) -> str:
    """
    Normalizza un URL per la deduplicazione:
    rimuove trailing slash, frammenti e parametri UTM/tracking comuni.
    """
    try:
        parsed = urlparse(url)
        query = '&'.join(
            p for p in (parsed.query or '').split('&')
            if not any(p.lower().startswith(t) for t in
                       ['utm_', 'ref=', 'source=', 'medium=', 'campaign='])
        )
        normalizzato = urlunparse((
            parsed.scheme,
            parsed.netloc.lower(),
            parsed.path.rstrip('/'),
            parsed.params,
            query,
            ''   # rimuovi frammento
        ))
        return normalizzato
    except Exception:
        return url


# ─── NOTIZIE REDAZIONE ──────────────────────────────────────────────────────────

def carica_notizie_redazione() -> list:
    """Legge i file Markdown pubblicati da Sveltia CMS."""
    notizie = []
    percorsi = sorted(
        glob.glob(os.path.join(PERCORSO_CONTENT_NOTIZIE, '*.md')),
        reverse=True
    )
    if not percorsi:
        print(f'  ℹ Nessun articolo in {PERCORSO_CONTENT_NOTIZIE}/')
        return []
    for filepath in percorsi:
        try:
            post = frontmatter.load(filepath)
            slug = os.path.splitext(os.path.basename(filepath))[0]
            notizie.append({
                'slug':     slug,
                'titolo':   post.get('title', '') or '',
                'data':     str(post.get('date', '')) or '',
                'autore':   post.get('author', 'Redazione Calcio H24') or 'Redazione Calcio H24',
                'immagine': (post.get('image', '') or '').lstrip('/'),
                'sommario': post.get('description', '') or '',
                'testo':    post.content or '',
            })
        except Exception as e:
            print(f'  ✗ Errore lettura {filepath}: {e}')
    print(f'  ✓ Notizie redazione: {len(notizie)} articoli')
    return notizie


# ─── SALVATAGGIO ────────────────────────────────────────────────────────────────

def salva_json(percorso: str, dati: dict) -> None:
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    print(f'  ✓ Salvato: {percorso}')


# ─── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    ora = datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    print(f'\n🚀 Calcio H24 — Aggiornamento del {ora}\n')

    # 0. Diagnostica API
    print('🔍 Diagnostica API-Football...')
    log_status_api()

    # 1. Classifiche con auto-detect e fallback
    stagione = stagione_corrente_calcio()
    print(f'\n📊 Classifiche (stagione di partenza: {stagione}/{stagione+1})...')

    s_serie_a,   serie_a   = scarica_classifica_con_fallback(LEAGUE_SERIE_A,   stagione)
    s_champions, champions = scarica_classifica_con_fallback(LEAGUE_CHAMPIONS, stagione)

    salva_json(PERCORSO_CLASSIFICHE, {
        'aggiornamento':    ora,
        'stagione_serie_a': s_serie_a,    # int o None
        'stagione_champions': s_champions,
        'serie_a':          serie_a,
        'champions':        champions,
    })

    # 2. Feed RSS
    print('\n📰 Feed RSS...')
    notizie_rss = []
    for nome, url in RSS_FEEDS.items():
        notizie_rss.extend(scarica_feed_rss(nome, url))

    # Deduplicazione per URL normalizzato (gestisce trailing slash e UTM)
    visti = set()
    notizie_uniche = []
    for n in notizie_rss:
        chiave = normalizza_url(n.get('link', ''))
        if chiave and chiave not in visti:
            visti.add(chiave)
            notizie_uniche.append(n)

    # Ordinamento per data decrescente (più recenti prima)
    notizie_uniche.sort(key=lambda n: n.pop('_dt'), reverse=True)

    print(f'\n  ✓ Totale notizie (deduplicato, ordinato): {len(notizie_uniche)}')
    salva_json(PERCORSO_NOTIZIE_RSS, {
        'aggiornamento': ora,
        'dalla_rete':    notizie_uniche,
    })

    # 3. Notizie redazione
    print('\n✍️  Notizie redazione...')
    salva_json(PERCORSO_NOTIZIE_RED, {
        'aggiornamento': ora,
        'notizie':       carica_notizie_redazione(),
    })

    print(f'\n✅ Completato — {ora}\n')


if __name__ == '__main__':
    main()
