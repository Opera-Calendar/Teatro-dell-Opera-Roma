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
    return BeautifulSoup(response.content, 'html.parser')


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
    # Never read navigation labels (e.g. 'Scuola di Canto Corale') as cast/crew.
    season_index = next((i for i, v in enumerate(lines) if re.search(r'^Stagione\s+20\d{2}', v, re.I)), None)
    if season_index is not None:
        lines = lines[season_index:]
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
    # The conductor is the person named immediately under 'Direttore' on
    # the actual production page (the navigation has already been removed).
    conductor = ''
    for i, t in enumerate(lines[:cut]):
        if t.casefold() in {'direttore', 'direttore musicale', 'direzione musicale'}:
            for candidate in lines[i+1:min(i+5,cut)]:
                if candidate and candidate.casefold() not in {'regia', 'direttore', 'personaggi e interpreti'}:
                    conductor = candidate
                    break
            break

    # Do not misattribute alternating singers to specific dates. The full,
    # date-specific roster remains available through the source URL.
    # Operaroma mixes day numbers and multiple artists within each cast row.

    section = lines[cut+1:]
    preview_at = next((i for i,t in enumerate(section) if 'anteprima giovani' in t.casefold()), len(section))
    section = section[:preview_at]
    # Use pattern recognition on the flattened DATE SECTION rather than
    # requiring individual HTML nodes at precise indices. The site's DOM
    # fragments date labels differently across first nights and repeats.
    date_text = ' '.join(section)
    pattern = re.compile(
        r'(?<!\w)(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)'
        r'\s+(\d{1,2})\s+'
        r'(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre|gen|feb|mar|apr|mag|giu|lug|ago|set|ott|nov|dic)\.?'
        r'\s+(?:ORE\s*)?(\d{1,2}):(\d{2})\b', re.I)
    events = []
    seen = set()
    for match in pattern.finditer(date_text):
        day, monthname, hour, minute = match.groups()
        month = MONTHS[monthname.casefold()]
        year = season_start if month >= 11 else season_end
        try:
            start = datetime(year, month, int(day), int(hour), int(minute), tzinfo=TZ)
        except ValueError:
            continue
        if start.date() < datetime.now(TZ).date() - timedelta(days=45):
            continue
        key = start.isoformat()
        if key in seen:
            continue
        seen.add(key)
        desc = [f'Teatro dell’Opera di Roma – {title}']
        if conductor:
            desc.append('Dirigent: ' + conductor)
        desc.append('Besetzung: Bitte tagesaktuelle Besetzung auf der offiziellen Produktionsseite prüfen; Alternativbesetzungen können je Aufführung variieren.')
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
    if OUT.exists():
        try:
            old_count = OUT.read_bytes().count(b'BEGIN:VEVENT')
            if old_count >= 10 and len(records) < old_count * 0.65:
                raise SystemExit(f'Only {len(records)} events vs {old_count} previously; preserving existing calendar.')
        except OSError:
            pass
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
