/* ═══════════════════════════════════════════════════════════════
   app.js — Calcio H24
   Carica dati pre-generati da update.py e li renderizza nel DOM.
   Gestisce: tab classifiche, notizie rete, notizie redazione,
             menu mobile, scroll header, footer aggiornamento.
   ═══════════════════════════════════════════════════════════════ */

'use strict';

/* ── Configurazione percorsi file JSON ───────────────────────── */
const CFG = {
  classifiche:      'data/classifiche.json',
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
  inizializzaTab();
  inizializzaNavSmooth();
  caricaClassifiche();
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
   TAB CLASSIFICHE — Selezione Serie A / Champions
══════════════════════════════════════════════════════════════ */
function inizializzaTab() {
  const btns   = document.querySelectorAll('.tab-btn');
  const panels = document.querySelectorAll('.tab-content');
  const legenda = document.getElementById('legenda-zone');

  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;

      // Aggiorna stato bottoni
      btns.forEach(b => {
        b.classList.toggle('active', b === btn);
        b.setAttribute('aria-selected', String(b === btn));
      });

      // Mostra il pannello corretto, nasconde gli altri
      panels.forEach(p => p.classList.toggle('active', p.id === `tab-${target}`));

      // La legenda zone è significativa solo per la Serie A
      if (legenda) legenda.style.opacity = target === 'serie-a' ? '1' : '0.3';
    });
  });
}

/* ══════════════════════════════════════════════════════════════
   CLASSIFICHE — Fetch + render
══════════════════════════════════════════════════════════════ */
async function caricaClassifiche() {
  try {
    const res  = await fetch(CFG.classifiche);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const dati = await res.json();

    // Aggiorna la nota di aggiornamento e il footer
    if (dati.aggiornamento) {
      const nota = document.getElementById('aggiornamento-classifiche');
      if (nota) nota.textContent = `Dati aggiornati: ${dati.aggiornamento}`;

      const footer = document.getElementById('ultimo-aggiornamento');
      if (footer) footer.textContent = dati.aggiornamento;
    }

    renderClassifica('classifica-serie-a',  dati.serie_a  || [], 'serie_a');
    renderClassifica('classifica-champions', dati.champions || [], 'champions');

  } catch (err) {
    console.error('[CalcioH24] Errore classifiche:', err);
    ['classifica-serie-a', 'classifica-champions'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '<div class="errore">⚠️ Classifica non disponibile. Aggiornamento previsto domani alle 07:00.</div>';
    });
  }
}

/**
 * Costruisce e inserisce la tabella classifica nel DOM.
 * @param {string} id      - ID dell'elemento contenitore
 * @param {Array}  squadre - Array di oggetti squadra normalizzati
 * @param {string} tipo    - 'serie_a' | 'champions' (per zone colorate)
 */
function renderClassifica(id, squadre, tipo) {
  const el = document.getElementById(id);
  if (!el) return;

  if (!squadre || squadre.length === 0) {
    el.innerHTML = '<div class="nessuna-notizia">⏳ Nessun dato disponibile. Il primo aggiornamento avverrà domani alle 07:00.</div>';
    return;
  }

  const righe = squadre.map(s => {
    const zona  = calcolaZona(s.posizione, tipo, squadre.length);
    const forma = renderForma(s.forma || '');
    const logo  = s.logo
      ? `<img src="${s.logo}" alt="" class="logo-squadra" loading="lazy" onerror="this.style.visibility='hidden'">`
      : '<span style="width:24px;display:inline-block"></span>';

    return `
      <tr class="${zona}">
        <td class="pos">${s.posizione}</td>
        <td>
          <div class="cella-squadra">${logo}
            <span class="nome-squadra">${sanifica(s.squadra)}</span>
          </div>
        </td>
        <td>${s.giocate}</td>
        <td>${s.vinte}</td>
        <td class="col-mobile-hidden">${s.pareggiate}</td>
        <td class="col-mobile-hidden">${s.perse}</td>
        <td class="col-mobile-hidden">${s.gol_fatti}</td>
        <td class="col-mobile-hidden">${s.gol_subiti}</td>
        <td class="col-mobile-hidden">${s.differenza_reti >= 0 ? '+' : ''}${s.differenza_reti}</td>
        <td><div class="forma">${forma}</div></td>
        <td class="punti-bold">${s.punti}</td>
      </tr>`;
  }).join('');

  el.innerHTML = `
    <table class="classifica-table" role="table">
      <thead>
        <tr>
          <th>#</th>
          <th style="text-align:left">Squadra</th>
          <th title="Partite giocate">PG</th>
          <th title="Vittorie">V</th>
          <th class="col-mobile-hidden" title="Pareggi">N</th>
          <th class="col-mobile-hidden" title="Sconfitte">P</th>
          <th class="col-mobile-hidden" title="Gol fatti">GF</th>
          <th class="col-mobile-hidden" title="Gol subiti">GS</th>
          <th class="col-mobile-hidden" title="Differenza reti">DR</th>
          <th title="Forma recente (ultime 5)">Forma</th>
          <th title="Punti">Pt</th>
        </tr>
      </thead>
      <tbody>${righe}</tbody>
    </table>`;
}

/**
 * Restituisce la classe CSS per la zona di classifica.
 * Per la Serie A: Champions (1-4), Europa (5), Conference (6), Retrocessione (ultimi 3).
 * Per la Champions (fase campionato 2024-25): prime 8 agli ottavi, 9-24 playoff, 25-36 fuori.
 */
function calcolaZona(pos, tipo, totale) {
  if (tipo === 'serie_a') {
    if (pos <= 4)             return 'zona-champions';
    if (pos === 5)            return 'zona-europa';
    if (pos === 6)            return 'zona-conference';
    if (pos > totale - 3)     return 'zona-retrocessione';
  }
  // Champions League: formato campionato unico 2024-25
  if (tipo === 'champions') {
    if (pos <= 8)             return 'zona-champions';   // ottavi diretti
    if (pos <= 24)            return 'zona-europa';       // playoff
    return 'zona-retrocessione'; // eliminati
  }
  return '';
}

/**
 * Genera i quadratini colorati per la forma recente (es. "WDWLW").
 * Prende solo le ultime 5 partite.
 */
function renderForma(formaStr) {
  return formaStr
    .slice(-5)
    .split('')
    .map(f => {
      const cls   = f === 'W' ? 'forma-w' : f === 'D' ? 'forma-d' : 'forma-l';
      const label = f === 'W' ? 'Vittoria' : f === 'D' ? 'Pareggio' : 'Sconfitta';
      return `<span class="${cls}" title="${label}"></span>`;
    })
    .join('');
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
