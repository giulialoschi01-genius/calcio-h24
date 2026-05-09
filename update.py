#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update.py — Calcio H24
Aggiornamento giornaliero: classifiche + notizie RSS + notizie redazione.
Le API Key vengono lette dalle variabili d'ambiente GitHub Secrets.
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
STAGIONE         = 2025  # FIX: era 2024

# Percorsi relativi alla root del repository (dove viene eseguito lo script)
BASE_DIR               = os.path.dirname(os.path.abspath(__file__))
PERCORSO_CLASSIFICHE   = os.path.join(BASE_DIR, 'data', 'classifiche.json')
PERCORSO_NOTIZIE_RSS   = os.path.join(BASE_DIR, 'data', 'notizie_rss.json')
PERCORSO_NOTIZIE_RED   = os.path.join(BASE_DIR, 'data', 'notizie_redazione.json')
PERCORSO_CONTENT_NOTIZIE = os.path.join(BASE_DIR, 'content', 'notizie')

RSS_FEEDS = {
    'Corriere dello Sport': 'https://www.corrieredellosport.it/rss',
    'Tuttosport':           'https://www.tuttosport.com/rss/home.xml',
    'Sky Sport':            'https://feeds.sky.it/skysport/rss.xml',
    'Calciomercato.com':    'https://www.calciomercato.com/rss',
}

MAX_PER_FEED    = 3
RSS_TIMEOUT     = 12   # secondi
API_TIMEOUT     = 20   # secondi
USER_AGENT      = 'Mozilla/5.0 (compatible; CalcioH24Bot/1.0)'


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


# ─── CLASSIFICHE ────────────────────────────────────────────────────────────────

def scarica_classifica(league_id: int, stagione: int) -> list:
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

        standings_raw = dati['response'][0]['league']['standings']

        # FIX UCL formato svizzero 2024/25:
        # standings_raw può essere lista di liste (gruppi) o lista piatta.
        # Appiattimento robusto: raccoglie tutte le squadre da tutti i sotto-gruppi.
        squadre = []
        for elemento in standings_raw:
            if isinstance(elemento, list):
                squadre.extend(elemento)
            elif isinstance(elemento, dict):
                squadre.append(elemento)

        return [normalizza_squadra(s) for s in squadre]

    except (KeyError, IndexError) as e:
        print(f'  ✗ Struttura risposta imprevista (league {league_id}): {e}')
        return []
    except requests.RequestException as e:
        print(f'  ✗ Errore HTTP (league {league_id}): {e}')
        return []


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
    Converte una stringa data RSS (RFC 2822 o ISO) in datetime con timezone.
    Ritorna datetime.min in caso di errore (la notizia va in fondo all'ordinamento).
    """
    if not data_raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return parsedate_to_datetime(data_raw)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(data_raw)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def scarica_feed_rss(nome: str, url: str) -> list:
    """
    Scarica e analizza un feed RSS.
    Usa requests con timeout esplicito prima di passare il contenuto a feedparser.
    """
    notizie = []
    try:
        # FIX: requests con timeout → niente blocchi infiniti
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
        # Rimuovi parametri di tracking
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

    # 1. Classifiche
    print('📊 Classifiche...')
    serie_a  = scarica_classifica(LEAGUE_SERIE_A, STAGIONE)
    champions = scarica_classifica(LEAGUE_CHAMPIONS, STAGIONE)
    salva_json(PERCORSO_CLASSIFICHE, {
        'aggiornamento': ora,
        'serie_a':       serie_a,
        'champions':     champions,
    })

    # 2. Feed RSS
    print('\n📰 Feed RSS...')
    notizie_rss = []
    for nome, url in RSS_FEEDS.items():
        notizie_rss.extend(scarica_feed_rss(nome, url))

    # FIX: Deduplicazione per URL normalizzato (gestisce trailing slash e UTM)
    visti = set()
    notizie_uniche = []
    for n in notizie_rss:
        chiave = normalizza_url(n.get('link', ''))
        if chiave and chiave not in visti:
            visti.add(chiave)
            notizie_uniche.append(n)

    # FIX: Ordinamento per data decrescente (più recenti prima)
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
