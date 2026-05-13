/* ═══════════════════════════════════════════════════════════════
   app.js — Calcio H24
   Carica dati pre-generati da update.py e li renderizza nel DOM.
   Gestisce: notizie rete, notizie redazione,
             menu mobile, scroll header, footer aggiornamento.
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ── Configurazione percorsi file JSON ───────────────────────── */
const CFG = {
  notizieRete:      'data/notizie_rss.json',
  notizieRedazione: 'data/notizie_redazione.json',
  maxNotizieRete:   20,   // notizie esterne da mostrare
  maxRedazione:     5,    // nostre notizie da mostrare
};

/* ══════════════════════════════════════════════════════════════
   INIT — Avvio al caricamento del DOM
══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  inizializzaMenuMobile();
  inizializzaScrollHeader();
  inizializzaNavSmooth();
  caricaNotizieRete();
  caricaNotizieRedazione();
});

/* ══════════════════════════════════════════════════════════════
   MENU MOBILE — Apertura/chiusura hamburger
══════════════════════════════════════════════════════════════ */
function inizializzaMenuMobile() {
  const toggle = document.getElementById('menuToggle');
  const nav    = document.getElementById('nav');
  if (!toggle || !nav) return;

  toggle.addEventListener('click', () => {
    const aperto = nav.classList.toggle('aperto');
    toggle.classList.toggle('aperto', aperto);
    toggle.setAttribute('aria-expanded', String(aperto));
  });

  // Chiude il menu quando l'utente clicca un link di navigazione
  nav.querySelectorAll('.nav-link').forEach(link =>
    link.addEventListener('click', () => {
      nav.classList.remove('aperto');
      toggle.classList.remove('aperto');
      toggle.setAttribute('aria-expanded', 'false');
    })
  );

  // Chiude cliccando fuori dal menu
  document.addEventListener('click', e => {
    if (!nav.contains(e.target) && !toggle.contains(e.target)) {
      nav.classList.remove('aperto');
      toggle.classList.remove('aperto');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
}

/* ══════════════════════════════════════════════════════════════
   HEADER — Ombra dinamica allo scroll
══════════════════════════════════════════════════════════════ */
function inizializzaScrollHeader() {
  const header = document.getElementById('header');
  if (!header) return;
  window.addEventListener('scroll', () => {
    header.style.boxShadow = window.scrollY > 60
      ? '0 4px 30px rgba(0,0,0,0.55)'
      : '0 2px 20px rgba(0,0,0,0.4)';
  }, { passive: true });
}

/* ══════════════════════════════════════════════════════════════
   NAVIGAZIONE SMOOTH — Offset per header fisso
══════════════════════════════════════════════════════════════ */
function inizializzaNavSmooth() {
  const OFFSET_HEADER = 68; // altezza header in px
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', e => {
      const id = link.getAttribute('href');
      const target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      const top = target.getBoundingClientRect().top + window.scrollY - OFFSET_HEADER;
      window.scrollTo({ top, behavior: 'smooth' });
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   NOTIZIE DALLA RETE — Fetch + render
   Mostra: titolo, immagine anteprima, fonte, link esterno.
   NESSUN testo integrale (rispetto del copyright degli editori).
══════════════════════════════════════════════════════════════ */
async function caricaNotizieRete() {
  const el = document.getElementById('notizie-rete');
  if (!el) return;

  try {
    const res  = await fetch(CFG.notizieRete);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const dati = await res.json();

    // Aggiorna data nel footer con timestamp ultimo aggiornamento RSS
    if (dati.aggiornamento) {
      const footer = document.getElementById('ultimo-aggiornamento');
      if (footer) footer.textContent = dati.aggiornamento;
    }

    const lista = (dati.dalla_rete || []).slice(0, CFG.maxNotizieRete);

    if (lista.length === 0) {
      el.innerHTML = '<div class="nessuna-notizia">Nessuna notizia disponibile al momento.</div>';
      return;
    }
    el.innerHTML = lista.map(renderCardNotiziaRete).join('');

  } catch (err) {
    console.error('[CalcioH24] Errore notizie rete:', err);
    el.innerHTML = '<div class="errore">⚠️ Impossibile caricare le notizie. Riprova più tardi.</div>';
  }
}

/**
 * Genera l'HTML di una card notizia dalla rete.
 * È un <a> cliccabile che punta alla fonte originale.
 */
function renderCardNotiziaRete(n) {
  const titolo = sanifica(n.titolo || 'Leggi la notizia →');
  const fonte  = sanifica(n.fonte  || '');
  const data   = formattaData(n.data);

  // Immagine anteprima — se assente mostra placeholder con emoji
  const img = n.immagine
    ? `<img src="${n.immagine}" alt="" class="card-notizia-img" loading="lazy"
         onerror="this.outerHTML='<div class=\\'card-notizia-img-placeholder\\'>⚽</div>'">`
    : '<div class="card-notizia-img-placeholder">⚽</div>';

  return `
    <a href="${n.link}" target="_blank" rel="noopener noreferrer" class="card-notizia"
       title="Leggi su ${fonte}">
      ${img}
      <div class="card-notizia-body">
        <div class="card-notizia-fonte">${fonte}</div>
        <div class="card-notizia-titolo">${titolo}</div>
        <div class="card-notizia-data">${data}</div>
      </div>
    </a>`;
}

/* ══════════════════════════════════════════════════════════════
   LE NOSTRE NOTIZIE — Fetch + render
   Contenuto pubblicato via Decap CMS → data/notizie_redazione.json
══════════════════════════════════════════════════════════════ */
async function caricaNotizieRedazione() {
  const el = document.getElementById('notizie-redazione');
  if (!el) return;

  try {
    const res  = await fetch(CFG.notizieRedazione);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const dati = await res.json();

    const lista = (dati.notizie || []).slice(0, CFG.maxRedazione);

    if (lista.length === 0) {
      el.innerHTML = `
        <div class="nessuna-notizia">
          ✍️ Nessun articolo ancora pubblicato.<br>
          <small>Accedi all'<a href="admin/">Area Redazione</a> per scrivere il primo!</small>
        </div>`;
      return;
    }
    el.innerHTML = lista.map(renderCardNostraNotiziaId).join('');

  } catch {
    // File non ancora creato (primo avvio): mostra stato vuoto, non errore
    const el2 = document.getElementById('notizie-redazione');
    if (el2) {
      el2.innerHTML = `
        <div class="nessuna-notizia">
          ✍️ Nessun articolo ancora pubblicato.<br>
          <small>Accedi all'<a href="admin/">Area Redazione</a> per scrivere il primo!</small>
        </div>`;
    }
  }
}

/**
 * Genera l'HTML di una card "nostra notizia" (più ampia, con sommario).
 */
function renderCardNostraNotiziaId(n) {
  const titolo   = sanifica(n.titolo   || 'Senza titolo');
  const autore   = sanifica(n.autore   || 'Redazione');
  const sommario = sanifica(n.sommario || '');
  const data     = formattaData(n.data);

  const img = n.immagine
    ? `<img src="${n.immagine}" alt="${titolo}" class="card-nostra-img" loading="lazy">`
    : '';

  return `
    <div class="card-nostra-notizia">
      ${img}
      <div class="card-nostra-body">
        <div class="card-nostra-meta">
          <span class="card-nostra-autore">✍️ ${autore}</span>
          <span>${data}</span>
        </div>
        <div class="card-nostra-titolo">${titolo}</div>
        ${sommario ? `<div class="card-nostra-sommario">${sommario}</div>` : ''}
      </div>
    </div>`;
}

/* ══════════════════════════════════════════════════════════════
   UTILITY — Funzioni di supporto
══════════════════════════════════════════════════════════════ */

/**
 * Formatta una data ISO 8601 in formato italiano "gg/mm/aaaa HH:MM".
 * @param {string} dataStr - Stringa data grezza
 * @returns {string}
 */
function formattaData(dataStr) {
  if (!dataStr) return '';
  try {
    const d = new Date(dataStr);
    if (isNaN(d.getTime())) return dataStr;
    return d.toLocaleString('it-IT', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return dataStr;
  }
}

/**
 * Sanifica il testo per prevenire XSS:
 * converte caratteri speciali HTML in entità sicure.
 * @param {string} testo
 * @returns {string}
 */
function sanifica(testo) {
  if (!testo) return '';
  const div = document.createElement('div');
  div.textContent = testo;
  return div.innerHTML;
}
