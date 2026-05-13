#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update.py — Calcio H24
Aggiornamento giornaliero: notizie RSS + notizie redazione.

Versione del 09/05/2026:
- Rimossa la sezione classifiche (dipendeva da API-Football, piano Free
  non includeva la stagione corrente: forniva solo stagioni passate).
- Unica fonte RSS: Corriere dello Sport — Calcio (verificato).
- parse_data_rss restituisce sempre datetime aware (UTC) per evitare
  TypeError nell'ordinamento.
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

# Percorsi relativi alla root del repository (dove viene eseguito lo script)
BASE_DIR                 = os.path.dirname(os.path.abspath(__file__))
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

    # 1. Feed RSS
    print('📰 Feed RSS...')
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

    # 2. Notizie redazione
    print('\n✍️  Notizie redazione...')
    salva_json(PERCORSO_NOTIZIE_RED, {
        'aggiornamento': ora,
        'notizie':       carica_notizie_redazione(),
    })

    print(f'\n✅ Completato — {ora}\n')


if __name__ == '__main__':
    main()
