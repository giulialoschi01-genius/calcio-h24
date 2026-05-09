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
import time
from datetime import datetime, timezone

import requests
import feedparser
import frontmatter

# ─── CONFIGURAZIONE ────────────────────────────────────────────

API_FOOTBALL_KEY = os.environ.get('API_FOOTBALL_KEY', '')
NEWSAPI_KEY      = os.environ.get('NEWSAPI_KEY', '')

LEAGUE_SERIE_A   = 135
LEAGUE_CHAMPIONS = 2
STAGIONE         = 2024

# ── Cinque fonti RSS scelte ────────────────────────────────────
RSS_FEEDS = {
    'Gazzetta dello Sport': 'https://www.gazzetta.it/rss/calcio.xml',
    'Corriere dello Sport': 'https://www.corrieredellosport.it/rss',
    'Tuttosport':           'https://www.tuttosport.com/rss/home.xml',
    'Sky Sport':            'https://feeds.sky.it/skysport/rss.xml',
    'Calciomercato.com':    'https://www.calciomercato.com/rss',
}

# NewsAPI disattivato — solo RSS
NEWSAPI_KEYWORDS = []

PERCORSO_CLASSIFICHE     = 'data/classifiche.json'
PERCORSO_NOTIZIE_RSS     = 'data/notizie_rss.json'
PERCORSO_NOTIZIE_RED     = 'data/notizie_redazione.json'
PERCORSO_CONTENT_NOTIZIE = 'content/notizie'

MAX_PER_FEED = 15

# ─── CLASSIFICHE ───────────────────────────────────────────────

def scarica_classifica(league_id: int, stagione: int) -> list:
    if not API_FOOTBALL_KEY:
        print('  ⚠ API_FOOTBALL_KEY non impostata — skip classifica')
        return []

    url     = 'https://v3.football.api-sports.io/standings'
    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    params  = {'league': league_id, 'season': stagione}

    try:
        risposta = requests.get(url, headers=headers, params=params, timeout=20)
        risposta.raise_for_status()
        dati = risposta.json()

        standings_raw = dati['response'][0]['league']['standings']
        standings = standings_raw[0] if isinstance(standings_raw[0], list) else standings_raw
        return [normalizza_squadra(s) for s in standings]

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
        'posizione':       entry.get('rank', 0),
        'squadra':         team.get('name', ''),
        'logo':            team.get('logo', ''),
        'giocate':         stats.get('played', 0),
        'vinte':           stats.get('win', 0),
        'pareggiate':      stats.get('draw', 0),
        'perse':           stats.get('lose', 0),
        'gol_fatti':       gol.get('for', 0),
        'gol_subiti':      gol.get('against', 0),
        'differenza_reti': entry.get('goalsDiff', 0),
        'punti':           entry.get('points', 0),
        'forma':           entry.get('form', ''),
        'descrizione':     entry.get('description', ''),
    }

# ─── RSS ────────────────────────────────────────────────────────

def estrai_immagine_entry(entry) -> str:
    """Cerca immagine anteprima nei campi RSS più comuni."""

    # 1. media:thumbnail
    try:
        if entry.media_thumbnail:
            return entry.media_thumbnail[0].get('url', '')
    except AttributeError:
        pass

    # 2. media:content
    try:
        if entry.media_content:
            url = entry.media_content[0].get('url', '')
            if url:
                return url
    except AttributeError:
        pass

    # 3. enclosure immagine
    try:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href', '')
    except AttributeError:
        pass

    # 4. tag <img> nel sommario HTML
    sommario = getattr(entry, 'summary', '') or ''
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', sommario)
    if match:
        url = match.group(1)
        if not any(x in url for x in ['1x1', 'pixel', 'tracking', 'spacer']):
            return url

    return ''


def scarica_feed_rss(nome: str, url: str) -> list:
    """
    Scarica e analizza un feed RSS.
    NON filtra per data (troppo inaffidabile sui feed italiani).
    NON scarta per bozo (molti feed validi hanno piccoli difetti XML).
    """
    notizie = []
    try:
        # Header User-Agent: evita blocchi da parte di alcuni server
        feed = feedparser.parse(
            url,
            agent='Mozilla/5.0 (compatible; CalcioH24Bot/1.0)'
        )

        if not feed.entries:
            print(f'  ✗ {nome}: feed vuoto o irraggiungibile (url: {url})')
            return []

        
        for entry in feed.entries[:MAX_PER_FEED * 2]:
            titolo = (entry.get('title', '') or '').strip()
            if not titolo:
                continue

            # Filtro data: scarta notizie più vecchie di 10 giorni.
            # Usa published_parsed (già convertito da feedparser in struct_time).
            # Se la data manca o non è leggibile, accetta la notizia.
            data_parsed = entry.get('published_parsed') or entry.get('updated_parsed')
            if data_parsed:
                try:
                    eta_giorni = (time.time() - time.mktime(data_parsed)) / 86400
                    if eta_giorni > 30:
                        continue
                except Exception:
                    pass  # data non convertibile → accetta

            link     = entry.get('link', '')
            data_raw = entry.get('published', entry.get('updated', ''))
            immagine = estrai_immagine_entry(entry)

            notizie.append({
                
                
                'fonte':    nome,
                'titolo':   titolo,
                'link':     link,
                'data':     data_raw,
                'immagine': immagine,
            })

        print(f'  ✓ {nome}: {len(notizie)} notizie')

    except Exception as e:
        print(f'  ✗ Errore RSS {nome}: {e}')

    return notizie

# ─── NOTIZIE REDAZIONE ─────────────────────────────────────────

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

# ─── SALVATAGGIO ────────────────────────────────────────────────

def salva_json(percorso: str, dati: dict) -> None:
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    print(f'  ✓ Salvato: {percorso}')

# ─── MAIN ───────────────────────────────────────────────────────

def main():
    ora = datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    print(f'\n🚀  Calcio H24 — Aggiornamento del {ora}\n')

    # 1. Classifiche
    print('📊  Classifiche...')
    serie_a   = scarica_classifica(LEAGUE_SERIE_A, STAGIONE)
    champions = scarica_classifica(LEAGUE_CHAMPIONS, STAGIONE)
    salva_json(PERCORSO_CLASSIFICHE, {
        'aggiornamento': ora,
        'serie_a':       serie_a,
        'champions':     champions,
    })

    # 2. Feed RSS
    print('\n📰  Feed RSS...')
    notizie_rss = []
    for nome, url in RSS_FEEDS.items():
        notizie_rss.extend(scarica_feed_rss(nome, url))

    # Deduplicazione per URL
    visti = set()
    notizie_uniche = []
    for n in notizie_rss:
        link = n.get('link', '')
        if link and link not in visti:
            visti.add(link)
            notizie_uniche.append(n)

    print(f'\n  ✓ Totale notizie (deduplicato): {len(notizie_uniche)}')
    salva_json(PERCORSO_NOTIZIE_RSS, {
        'aggiornamento': ora,
        'dalla_rete':    notizie_uniche,
    })

    # 3. Notizie redazione
    print('\n✍️   Notizie redazione...')
    salva_json(PERCORSO_NOTIZIE_RED, {
        'aggiornamento': ora,
        'notizie':       carica_notizie_redazione(),
    })

    print(f'\n✅  Completato — {ora}\n')


if __name__ == '__main__':
    main()
