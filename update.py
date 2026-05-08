#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update.py — Calcio H24
Script di aggiornamento giornaliero dati.
Eseguito ogni mattina alle 07:00 (ora italiana) da GitHub Actions.

Cosa fa:
  1. Scarica classifica Serie A e Champions League da API-Football
  2. Scarica notizie RSS da Gazzetta, Sky, Corriere, Tuttosport
  3. Scarica notizie da NewsAPI (keywords: Serie A, Champions League)
  4. Compila gli articoli di Decap CMS (content/notizie/*.md)
  5. Salva tutto in data/classifiche.json e data/notizie_rss.json
     e data/notizie_redazione.json

Le API Key vengono lette dalle variabili d'ambiente GitHub Secrets.
"""

import os
import json
import re
import glob
from datetime import datetime, timezone

import requests
import feedparser
import frontmatter  # pip install python-frontmatter

# ─── CONFIGURAZIONE ────────────────────────────────────────────────────────────

# Le chiavi API vengono iniettate da GitHub Secrets (NON hardcodarle mai!)
API_FOOTBALL_KEY = os.environ.get('API_FOOTBALL_KEY', '')
NEWSAPI_KEY      = os.environ.get('NEWSAPI_KEY', '')

# ID campionati su API-Football (v3)
LEAGUE_SERIE_A    = 135   # Serie A italiana
LEAGUE_CHAMPIONS  = 2     # UEFA Champions League
STAGIONE          = 2024  # Stagione corrente (2024-2025)

# Feed RSS degli editori italiani
RSS_FEEDS = {
    'Gazzetta dello Sport': 'https://www.gazzetta.it/rss/home.xml',
    'Sky Sport':            'https://feeds.sky.it/skysport/rss.xml',
    'Corriere dello Sport': 'https://www.corrieredellosport.it/rss',
    'Tuttosport':           'https://www.tuttosport.com/rss/home.xml',
}

# Parole chiave per filtrare le notizie RSS pertinenti al calcio
KEYWORDS_CALCIO = [
    'serie a', 'champions', 'calcio', 'partita', 'gol', 'squadra',
    'nazionale', 'inter', 'milan', 'juve', 'juventus', 'napoli', 'roma',
    'modena', 'lazio', 'fiorentina', 'atalanta', 'torino',
]

# Keyword per NewsAPI
NEWSAPI_KEYWORDS = ['Serie A', 'Champions League']

# Percorsi file di output
PERCORSO_CLASSIFICHE    = 'data/classifiche.json'
PERCORSO_NOTIZIE_RSS    = 'data/notizie_rss.json'
PERCORSO_NOTIZIE_RED    = 'data/notizie_redazione.json'
PERCORSO_CONTENT_NOTIZIE = 'content/notizie'

# Numero massimo notizie per fonte RSS
MAX_PER_FEED = 12

# ─── FUNZIONI CLASSIFICHE ──────────────────────────────────────────────────────

def scarica_classifica(league_id: int, stagione: int) -> list:
    """
    Chiama l'endpoint /standings di API-Football.
    Restituisce la lista di squadre normalizzata, o lista vuota in caso di errore.
    """
    if not API_FOOTBALL_KEY:
        print('  ⚠ API_FOOTBALL_KEY non impostata — skip classifica')
        return []

    url = 'https://v3.football.api-sports.io/standings'
    headers = {'x-apisports-key': API_FOOTBALL_KEY}
    params  = {'league': league_id, 'season': stagione}

    try:
        risposta = requests.get(url, headers=headers, params=params, timeout=20)
        risposta.raise_for_status()
        dati = risposta.json()

        # La risposta ha struttura: response[0].league.standings (lista di gruppi)
        standings_raw = dati['response'][0]['league']['standings']

        # In Serie A è una lista singola; in Champions potrebbe essere per gironi.
        # Dalla stagione 2024-25 UCL usa formato campionato (lista singola).
        if isinstance(standings_raw[0], list):
            # Formato a gironi: prende il primo gruppo (o appiattisce tutto)
            standings = standings_raw[0]
        else:
            standings = standings_raw

        return [normalizza_squadra(s) for s in standings]

    except (KeyError, IndexError) as e:
        print(f'  ✗ Struttura risposta imprevista (league {league_id}): {e}')
        return []
    except requests.RequestException as e:
        print(f'  ✗ Errore HTTP (league {league_id}): {e}')
        return []


def normalizza_squadra(entry: dict) -> dict:
    """
    Trasforma una entry grezza di API-Football nel formato
    semplificato usato dal frontend.
    """
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

# ─── FUNZIONI RSS ──────────────────────────────────────────────────────────────

def estrai_immagine_entry(entry) -> str:
    """
    Cerca l'URL dell'immagine anteprima in tutti i campi comuni dei feed RSS.
    Restituisce l'URL o stringa vuota se non trovata.
    """
    # 1. media:thumbnail (standard RSS)
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')

    # 2. media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        url = entry.media_content[0].get('url', '')
        if url:
            return url

    # 3. enclosure (podcast/media)
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href', '')

    # 4. Cerca tag <img> nel summary HTML
    sommario = getattr(entry, 'summary', '') or ''
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', sommario)
    if match:
        url = match.group(1)
        # Esclude immagini traccianti (1x1, pixel)
        if not any(x in url for x in ['1x1', 'pixel', 'tracking', 'spacer']):
            return url

    return ''


def e_notizia_calcio(titolo: str, sommario: str) -> bool:
    """
    Filtra le notizie tenendo solo quelle legate al calcio.
    Controlla se titolo o sommario contengono almeno una keyword.
    """
    testo = (titolo + ' ' + sommario).lower()
    return any(kw in testo for kw in KEYWORDS_CALCIO)


def scarica_feed_rss(nome: str, url: str) -> list:
    """
    Analizza un feed RSS con feedparser.
    Restituisce le notizie filtrate per tema calcio.
    """
    notizie = []
    try:
        # feedparser gestisce richiesta HTTP e parsing XML/Atom
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f'  ✗ Feed malformato o irraggiungibile: {nome}')
            return []

        for entry in feed.entries[:MAX_PER_FEED * 2]:  # legge più per poter filtrare
            titolo   = (entry.get('title', '') or '').strip()
            sommario = (entry.get('summary', '') or '').strip()

            # Salta notizie non calcistiche
            if not e_notizia_calcio(titolo, sommario):
                continue

            immagine = estrai_immagine_entry(entry)

            notizie.append({
                'fonte':    nome,
                'titolo':   titolo,
                'link':     entry.get('link', ''),
                'data':     entry.get('published', entry.get('updated', '')),
                'immagine': immagine,
            })

            if len(notizie) >= MAX_PER_FEED:
                break

        print(f'  ✓ RSS {nome}: {len(notizie)} notizie calcio trovate')

    except Exception as e:
        print(f'  ✗ Errore RSS {nome}: {e}')

    return notizie


# ─── FUNZIONI NEWSAPI ──────────────────────────────────────────────────────────

def scarica_newsapi(keyword: str) -> list:
    """
    Chiama NewsAPI per la keyword indicata.
    Restituisce lista di notizie o lista vuota in caso di errore.
    NewsAPI va chiamato server-side (il piano free blocca le richieste browser).
    """
    if not NEWSAPI_KEY:
        print('  ⚠ NEWSAPI_KEY non impostata — skip NewsAPI')
        return []

    url = 'https://newsapi.org/v2/everything'
    params = {
        'q':        keyword,
        'language': 'it',
        'sortBy':   'publishedAt',
        'pageSize': 15,
        'apiKey':   NEWSAPI_KEY,
    }

    try:
        risposta = requests.get(url, params=params, timeout=20)
        risposta.raise_for_status()
        dati = risposta.json()

        if dati.get('status') != 'ok':
            print(f'  ✗ NewsAPI errore per "{keyword}": {dati.get("message")}')
            return []

        articoli = []
        for art in dati.get('articles', []):
            # Salta le fonti "[Removed]" (contenuto rimosso da NewsAPI)
            if art.get('title', '') in ('[Removed]', None, ''):
                continue
            articoli.append({
                'fonte':    art.get('source', {}).get('name', 'NewsAPI'),
                'titolo':   (art.get('title', '') or '').strip(),
                'link':     art.get('url', ''),
                'data':     art.get('publishedAt', ''),
                'immagine': art.get('urlToImage', '') or '',
            })

        print(f'  ✓ NewsAPI "{keyword}": {len(articoli)} notizie')
        return articoli

    except requests.RequestException as e:
        print(f'  ✗ Errore NewsAPI "{keyword}": {e}')
        return []


# ─── FUNZIONI NOTIZIE REDAZIONE (DECAP CMS) ───────────────────────────────────

def carica_notizie_redazione() -> list:
    """
    Legge i file Markdown creati da Decap CMS in content/notizie/*.md
    e li converte in dizionari pronti per il JSON.
    Il frontmatter YAML contiene i metadati (titolo, data, autore, immagine).
    Il corpo del file è il testo dell'articolo in Markdown.
    """
    notizie = []

    # Cerca tutti i file .md nella cartella content/notizie/
    percorsi = sorted(
        glob.glob(os.path.join(PERCORSO_CONTENT_NOTIZIE, '*.md')),
        reverse=True  # Ordine cronologico inverso (più recenti prima)
    )

    if not percorsi:
        print(f'  ℹ Nessun articolo trovato in {PERCORSO_CONTENT_NOTIZIE}/')
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
                'immagine': post.get('image', '') or '',
                'sommario': post.get('description', '') or '',
                'testo':    post.content or '',
            })
        except Exception as e:
            print(f'  ✗ Errore lettura {filepath}: {e}')

    print(f'  ✓ Notizie redazione: {len(notizie)} articoli caricati')
    return notizie


# ─── SALVATAGGIO JSON ──────────────────────────────────────────────────────────

def salva_json(percorso: str, dati: dict) -> None:
    """
    Salva un dizionario come file JSON con indentazione leggibile.
    Crea le directory necessarie se non esistono.
    """
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    print(f'  ✓ Salvato: {percorso}')


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # Timestamp dell'aggiornamento (formato leggibile per l'utente)
    ora = datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')
    print(f'\n🚀  Calcio H24 — Aggiornamento del {ora}\n')

    # ── 1. CLASSIFICHE ─────────────────────────────────────────────────────────
    print('📊  Scaricamento classifiche...')
    serie_a   = scarica_classifica(LEAGUE_SERIE_A, STAGIONE)
    champions = scarica_classifica(LEAGUE_CHAMPIONS, STAGIONE)

    salva_json(PERCORSO_CLASSIFICHE, {
        'aggiornamento': ora,
        'serie_a':       serie_a,
        'champions':     champions,
    })

    # ── 2. FEED RSS ─────────────────────────────────────────────────────────────
    print('\n📰  Scaricamento feed RSS...')
    notizie_rss = []
    for nome, url in RSS_FEEDS.items():
        notizie_rss.extend(scarica_feed_rss(nome, url))

    # ── 3. NEWSAPI ──────────────────────────────────────────────────────────────
    print('\n🔍  Scaricamento NewsAPI...')
    notizie_news = []
    for keyword in NEWSAPI_KEYWORDS:
        notizie_news.extend(scarica_newsapi(keyword))

    # Combina RSS + NewsAPI e rimuove duplicati per URL
    visti = set()
    notizie_combinate = []
    for n in notizie_rss + notizie_news:
        link = n.get('link', '')
        if link and link not in visti:
            visti.add(link)
            notizie_combinate.append(n)

    print(f'\n  ✓ Totale notizie dalla rete (deduplicato): {len(notizie_combinate)}')

    salva_json(PERCORSO_NOTIZIE_RSS, {
        'aggiornamento': ora,
        'dalla_rete':    notizie_combinate,
    })

    # ── 4. NOTIZIE REDAZIONE (DECAP CMS) ───────────────────────────────────────
    print('\n✍️   Caricamento notizie redazione...')
    notizie_redazione = carica_notizie_redazione()

    salva_json(PERCORSO_NOTIZIE_RED, {
        'aggiornamento': ora,
        'notizie':       notizie_redazione,
    })

    # ── Fine ────────────────────────────────────────────────────────────────────
    print(f'\n✅  Aggiornamento completato — {ora}\n')


if __name__ == '__main__':
    main()
