#!/usr/bin/env python3
"""Opera di Roma / Teatro Costanzi only -> dynamic iCalendar feed.

The website has no documented public schedule API. This script parses its
public season cards and the individual production pages. If markup changes,
log a warning rather than overwrite a good calendar with an empty feed.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

BASE = 'https://www.operaroma.it/'
OUT = Path('opera-roma-costanzi.ics')
VENUE = 'Teatro Costanzi, Piazza Beniamino Gigli 7, Roma, Italia'
TZ = ZoneInfo('Europe/Rome')
HEADERS = {'User-Agent': 'OperaRomaCalendar/1.0 (personal calendar feed; contact via GitHub)'}
MONTHS = {'gen':1,'feb':2,'mar':3,'apr':4,'mag':5,'giu':6,'lug':7,'ago':8,'set':9,'ott':10,'nov':11,'dic':12,
          'gennaio':1,'febbraio':2,'marzo':3,'aprile':4,'maggio':5,'giugno':6,'luglio':7,'agosto':8,'settembre':9,'ottobre':10,'novembre':11,'dicembre':12}
WEEKDAYS = {'lunedì','martedì','mercoledì','giovedì','venerdì','sabato','domenica',
            'lunedi','martedi','mercoledi','giovedi','venerdi'}
# Reliable 2026/27 season links, in addition to automatically discovered new titles.
# These are NOT the source of performance dates: all dates are re-read live.
SEED_URLS = [
 'https://www.operaroma.it/spettacoli/the-rakes-progress/',
 'https://www.operaroma.it/spettacoli/andrea-chenier-3/',
 'https://www.operaroma.it/spettacoli/lucia-di-lammermoor/',
 'https://www.operaroma.it/spettacoli/i-due-foscari/',
 'https://www.operaroma.it/spettacoli/iphigenie-en-tauride/',
 'https://www.operaroma.it/spettacoli/una-lady-macbeth-del-distretto-di-mzensk/',
 'https://www.operaroma.it/spettacoli/il-trovatore-3/',
 'https://www.operaroma.it/spettacoli/la-rondine/',
]


def fetch(session, url):
    response = session.get(url, timeout=35)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def clean(s):
    return re.sub(r'\s+', ' ', s or '').strip()


def is_stage_opera_card(anchor):
    # On the season page, each card and calendar entry has category & venue.
    # Inspect local ancestors only, not the full season container.
    for parent in list(anchor.parents)[:5]:
        t = clean(parent.get_text(' ', strip=True))
        if not 20 <= len(t) <= 480:
            continue
        if 'Teatro Costanzi' not in t:
            continue
        if re.search(r'\bOpere in Concerto\b|\bBalletti\b|\bConcerti\b|\bLezioni', t, re.I):
            continue
        if re.search(r'\bOpere\b', t, re.I):
            return True
    return False


def discover_urls(session):
    soup = fetch(session, BASE)
    urls = set()
    for a in soup.select('a[href]'):
        u = urljoin(BASE, a['href']).split('#')[0].rstrip('/') + '/'
        if urlparse(u).netloc not in {'www.operaroma.it','operaroma.it'}:
            continue
        if '/spettacoli/' in u and is_stage_opera_card(a):
            urls.add(u)
    # Known current production pages are fallbacks to changes in card markup.
    urls.update(SEED_URLS)
    logging.info('Found %s potential production URLs', len(urls))
    return sorted(urls)


def portion(soup):
    # Production data precedes the cross-selling section 'Ti può interessare'.
    main = soup.select_one('main') or soup.body or soup
    lines = [clean(x) for x in main.stripped_strings]
    try:
        end = next(i for i, x in enumerate(lines) if 'Ti può interessare' in x)
        lines = lines[:end]
    except StopIteration:
        pass
    return lines


def year_for(lines):
    # The current site renders the season label after a lengthy navigation
    # menu. Searching the first 70 strings discarded it, causing every
    # 2026/27 production to fail with 'No season year'.
    joined = ' '.join(lines)
    match = re.search(r'Stagione\s+(20\d{2})\s*[/–-]\s*(20\d{2}|\d{2})(?!\d)', joined, re.I)
    if match:
        first, last = int(match.group(1)), int(match.group(2))
        if last < 100:
            last += (first // 100) * 100
        if last == first + 1:
            return first, last
    return None


def extract_events(lines, title, url, page_years=None):
    joined = ' '.join(lines)
    if not re.search(r'\bTeatro Costanzi\b', joined):
        return []
    # Do not accidentally use unrelated productions from suggested links.
    try:
        cut = next(i for i, x in enumerate(lines) if x.casefold() == 'le date')
    except StopIteration:
        logging.warning('No Le date section: %s', url)
        return []
    header = ' '.join(lines[:cut])
    if re.search(r'\bBalletto\b|\bCoreografia\b', header, re.I):
        return []
    if re.search(r'\bOpere in Concerto\b|\bConcerto\b', header[:220], re.I):
        return []
    if not ('musica di' in header.lower() or 'opera' in header.lower() or 'dramma' in header.lower()):
        logging.warning('Unknown genre, skipping %s', url)
        return []

    years = year_for(lines) or page_years
    if not years:
        logging.warning('No season year: %s', url)
        return []
    season_start, season_end = years
    conductor = ''
    for i, t in enumerate(lines[:cut]):
        if t.lower() in {'direttore', 'direttore musicale', 'direzione musicale'} and i+1 < cut:
            conductor = lines[i+1]
            break
    cast_lines = []
    for i,t in enumerate(lines[:cut]):
        if re.search(r'PERSONAGGI E INTERPRETI|PERSONAGGI E INTEPRETI|CAST|INTERPRETI', t, re.I):
            for v in lines[i+1:min(cut,i+25)]:
                if re.search(r'^Orchestra|^Produzione|^Nuova produzione|^Con sovratitoli|^Dove$', v, re.I):
                    break
                if len(v) >= 5:
                    cast_lines.append(v)
            break
    cast = '\n'.join(cast_lines[:12])

    # Read the official list: "venerdì / 27 / Nov / ORE 19:00 Turno A".
    # Preview performances are explicitly excluded.
    section = lines[cut+1:]
    preview_at = next((i for i,t in enumerate(section) if 'Anteprima giovani' in t), len(section))
    section = section[:preview_at]
    events = []
    seen = set()
    for i,t in enumerate(section):
        if t.casefold() not in WEEKDAYS:
            continue
        if i+3 >= len(section) or not re.fullmatch(r'\d{1,2}',section[i+1]):
            continue
        month = MONTHS.get(section[i+2].casefold().strip('.'))
        if not month:
            continue
        time_match = re.search(r'(?:ORE\s*)?(\d{1,2}):(\d{2})', section[i+3], re.I)
        if not time_match:
            continue
        year = season_start if month >= 11 else season_end
        # Opera Roma seasons start Nov and carry through Oct.
        try:
            start = datetime(year, month, int(section[i+1]), int(time_match.group(1)), int(time_match.group(2)),tzinfo=TZ)
        except ValueError:
            continue
        if start.date() < datetime.now(TZ).date() - timedelta(days=45):
            continue
        key = start.isoformat()
        if key in seen:
            continue
        seen.add(key)
        desc = [f'Teatro dell’Opera di Roma – {title}']
        if conductor: desc.append('Dirigent: '+conductor)
        if cast: desc.append('Besetzung (Angaben der Produktion; einzelne Termine können abweichen):\n'+cast)
        desc.extend(['Endzeit geschätzt (+3 h); bitte offizielle Dauer prüfen.',f'Quelle: {url}'])
        events.append((start, '\n\n'.join(desc)))
    return events


def get_production(session, url):
    soup = fetch(session, url)
    lines = portion(soup)
    h1 = soup.find('h1')
    title = clean(h1.get_text(' ',strip=True)) if h1 else ''
    if not title:
        return []
    page_years = year_for([clean(x) for x in soup.stripped_strings])
    return [(title, when, desc, url) for when, desc in extract_events(lines,title,url,page_years)]


def main():
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    session = requests.Session()
    session.headers.update(HEADERS)
    productions = discover_urls(session)
    records = {}
    for index,url in enumerate(productions,1):
        try:
            rows = get_production(session,url)
            logging.info('[%s/%s] %s: %s performances',index,len(productions),url,len(rows))
            for title,start,desc,src in rows:
                records[(title.casefold(),start)] = (title,start,desc,src)
        except requests.RequestException as e:
            logging.warning('Failed to fetch %s: %s',url,e)
    if not records:
        raise SystemExit('No valid future performances found: keeping the previous ICS unchanged.')
    cal = Calendar()
    cal.add('prodid','-//Opera Calendar//Opera di Roma Costanzi//EN')
    cal.add('version','2.0')
    cal.add('calscale','GREGORIAN')
    cal.add('x-wr-calname','Opera di Roma – Teatro Costanzi (Opera only)')
    cal.add('x-wr-timezone','Europe/Rome')
    now = datetime.now(timezone.utc)
    for title,start,description,url in sorted(records.values(),key=lambda x:x[1]):
        event = Event()
        signature = hashlib.sha256(f'{title}|{start.isoformat()}'.encode()).hexdigest()[:24]
        event.add('uid',f'{signature}@opera-roma-calendar')
        event.add('dtstamp',now)
        event.add('dtstart',start)
        event.add('dtend',start+timedelta(hours=3))
        event.add('summary',title)
        event.add('location',VENUE)
        event.add('description',description)
        event.add('url',url)
        cal.add_component(event)
    OUT.write_bytes(cal.to_ical())
    logging.info('Wrote %s (%s events)',OUT,len(records))


if __name__ == '__main__':
    main()
