# -*- coding: utf-8 -*-

import sys, re, os, json, time
from urllib.parse import parse_qs, urlparse, parse_qs as parse_query_string
import xbmc, xbmcgui, xbmcplugin, xbmcaddon, xbmcvfs

try:
    import requests
    from bs4 import BeautifulSoup
    HAS_DEPENDENCIES = True
except:
    HAS_DEPENDENCIES = False

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_DATA_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
if not os.path.exists(ADDON_DATA_PATH):
    os.makedirs(ADDON_DATA_PATH)

CACHE_FILE = os.path.join(ADDON_DATA_PATH, 'video_cache.json')
LATEST_CACHE_FILE = os.path.join(ADDON_DATA_PATH, 'latest_videos_cache.json')
METADATA_CACHE_FILE = os.path.join(ADDON_DATA_PATH, 'metadata_cache.json')
FORUM_BASE_URL = 'https://www.kodinerds.net/thread/13225-musik-tipps'
CACHE_VALIDITY = 86400

def log(msg, level=xbmc.LOGDEBUG):
    xbmc.log('[%s] %s' % (ADDON_ID, msg), level)

def get_cached_videos():
    if not os.path.exists(CACHE_FILE):
        return None, 0
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('videos', []), data.get('timestamp', 0)
    except:
        return None, 0

def save_cached_videos(videos):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'videos': videos, 'timestamp': int(time.time())}, f, indent=2)
    except:
        pass

def get_cached_latest_videos():
    if not os.path.exists(LATEST_CACHE_FILE):
        return None, 0
    try:
        with open(LATEST_CACHE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            videos = data.get('videos', [])

            # ABWAERTSKOMPATIBILITAET: Altes Format (String-Liste) -> Neues Format (Dict-Liste)
            if videos and isinstance(videos[0], str):
                log('Konvertiere alten Cache (Strings) zu neuem Format (Dicts)', xbmc.LOGINFO)
                videos = [{'video_id': vid, 'username': 'Unbekannt'} for vid in videos]

            return videos, data.get('timestamp', 0)
    except Exception as e:
        log('Fehler beim Laden des Latest-Cache: %s' % str(e), xbmc.LOGERROR)
        return None, 0

def save_cached_latest_videos(videos):
    try:
        with open(LATEST_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'videos': videos, 'timestamp': int(time.time())}, f, indent=2, ensure_ascii=False)
    except:
        pass

def get_cached_metadata():
    if not os.path.exists(METADATA_CACHE_FILE):
        return {}
    try:
        with open(METADATA_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_cached_metadata(metadata):
    try:
        with open(METADATA_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    except:
        pass

def fetch_youtube_metadata(video_id):
    if not HAS_DEPENDENCIES:
        return None
    try:
        url = 'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=%s&format=json' % video_id
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return {
                'title': data.get('title', 'Unbekannt'),
                'author': data.get('author_name', 'Unbekannter Kuenstler')
            }
    except:
        pass
    return None

def get_video_metadata_batch(video_ids):
    metadata_cache = get_cached_metadata()
    results = {}
    for video_id in video_ids:
        if video_id in metadata_cache:
            results[video_id] = metadata_cache[video_id]
        else:
            meta = fetch_youtube_metadata(video_id)
            if meta:
                results[video_id] = meta
                metadata_cache[video_id] = meta
                log('Metadaten geladen fuer %s: %s - %s' % (video_id, meta['author'], meta['title']))
            else:
                results[video_id] = {'title': video_id, 'author': 'YouTube'}
            time.sleep(0.2)
    save_cached_metadata(metadata_cache)
    return results

def get_page_count(soup):
    try:
        pagination = soup.find('woltlab-core-pagination')
        if pagination and pagination.get('count'):
            return int(pagination.get('count'))
        page_links = soup.find_all('a', href=re.compile(r'pageNo=\\d+'))
        max_page = 1
        for link in page_links:
            match = re.search(r'pageNo=(\\d+)', link.get('href', ''))
            if match:
                max_page = max(max_page, int(match.group(1)))
        return max_page
    except:
        return 1

def extract_youtube_id_from_url(url):
    try:
        parsed = urlparse(url)
        if 'youtube.com' in parsed.netloc and 'watch' in parsed.path:
            query_params = parse_query_string(parsed.query)
            if 'v' in query_params:
                return query_params['v'][0]
        if 'youtu.be' in parsed.netloc:
            video_id = parsed.path.strip('/')
            if video_id:
                return video_id.split('?')[0].split('&')[0]
        if 'youtube.com' in parsed.netloc and '/embed/' in parsed.path:
            return parsed.path.split('/embed/')[1].split('?')[0]
        if 'youtube-nocookie.com' in parsed.netloc and '/embed/' in parsed.path:
            return parsed.path.split('/embed/')[1].split('?')[0]
    except:
        pass
    return None

def scrape_youtube_videos_with_users(html_content):
    """Extrahiert Video-IDs MIT Benutzernamen in Reihenfolge"""
    if not HAS_DEPENDENCIES:
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    results = []

    # Alle Posts durchgehen
    posts = soup.find_all('article', class_='message')
    log('Gefunden: %d Posts auf dieser Seite' % len(posts))

    for post in posts:
        # Benutzername extrahieren
        username = 'Unbekannt'
        author_span = post.find('span', itemprop='name')
        if author_span:
            username = author_span.get_text(strip=True)

        # Post-Inhalt
        message_body = post.find('div', class_='messageBody')
        if not message_body:
            continue

        post_html = str(message_body)

        # Video-IDs in diesem Post finden
        video_ids_in_post = []

        # HTML iframes
        for match in re.finditer(r'youtube\\.com/embed/([a-zA-Z0-9_-]{11})', post_html):
            video_ids_in_post.append(match.group(1))
        for match in re.finditer(r'youtube-nocookie\\.com/embed/([a-zA-Z0-9_-]{11})', post_html):
            video_ids_in_post.append(match.group(1))

        # Direkte URLs
        for match in re.finditer(r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})', post_html):
            video_ids_in_post.append(match.group(1))
        for match in re.finditer(r'youtu\.be/([a-zA-Z0-9_-]{11})', post_html):
            video_ids_in_post.append(match.group(1))

        # BBCode [media] Tags
        for match in re.finditer(r'\[media\]([^\[]+)\[/media\]', post_html, re.IGNORECASE):
            url = match.group(1).replace('&amp;', '&')
            video_id = extract_youtube_id_from_url(url)
            if video_id and len(video_id) == 11:
                video_ids_in_post.append(video_id)

        # Duplikate im gleichen Post entfernen, aber User beibehalten
        seen_in_post = set()
        for vid in video_ids_in_post:
            if vid not in seen_in_post and len(vid) == 11:
                results.append({'video_id': vid, 'username': username})
                seen_in_post.add(vid)
                log('Video %s von User %s' % (vid, username))

    return results

def scrape_youtube_videos_from_page(html_content):
    """Alte Funktion fuer 'Alle Videos' ohne Benutzer"""
    video_ids = []
    findings = []

    for match in re.finditer(r'youtube\\.com/embed/([a-zA-Z0-9_-]{11})', html_content):
        findings.append((match.start(), match.group(1)))
    for match in re.finditer(r'youtube-nocookie\\.com/embed/([a-zA-Z0-9_-]{11})', html_content):
        findings.append((match.start(), match.group(1)))
    for match in re.finditer(r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})', html_content):
        findings.append((match.start(), match.group(1)))
    for match in re.finditer(r'youtu\.be/([a-zA-Z0-9_-]{11})', html_content):
        findings.append((match.start(), match.group(1)))

    for match in re.finditer(r'\[media\]([^\[]+)\[/media\]', html_content, re.IGNORECASE):
        url = match.group(1).replace('&amp;', '&')
        video_id = extract_youtube_id_from_url(url)
        if video_id and len(video_id) == 11:
            findings.append((match.start(), video_id))

    findings.sort(key=lambda x: x[0])
    seen = set()
    for pos, video_id in findings:
        if video_id not in seen and len(video_id) == 11:
            video_ids.append(video_id)
            seen.add(video_id)

    return video_ids

def scrape_latest_page():
    """Laedt letzte Seite MIT Benutzernamen"""
    if not HAS_DEPENDENCIES:
        return []
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(FORUM_BASE_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        page_count = get_page_count(soup)
        log('Thread hat %d Seiten' % page_count)

        if page_count > 1:
            r = requests.get('%s?pageNo=%d' % (FORUM_BASE_URL, page_count), headers=headers, timeout=15)
            r.raise_for_status()

        # Videos MIT Benutzernamen extrahieren
        results = scrape_youtube_videos_with_users(r.text)
        log('Letzte Seite: %d Videos mit Benutzern gefunden' % len(results))
        return results
    except Exception as e:
        log('Fehler beim Scraping: %s' % str(e), xbmc.LOGERROR)
        return []

def scrape_all_pages():
    if not HAS_DEPENDENCIES:
        return []
    all_video_ids = []
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        r = requests.get(FORUM_BASE_URL, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        page_count = get_page_count(soup)

        all_video_ids.extend(scrape_youtube_videos_from_page(r.text))

        progress = xbmcgui.DialogProgress()
        progress.create('Kodinerds Musik-Tipps', 'Durchsuche alle Thread-Seiten...')

        for page_num in range(2, page_count + 1):
            if progress.iscanceled():
                break
            progress.update(int((page_num / page_count) * 100), 'Seite %d von %d...' % (page_num, page_count))
            try:
                r = requests.get('%s?pageNo=%d' % (FORUM_BASE_URL, page_num), headers=headers, timeout=15)
                r.raise_for_status()
                all_video_ids.extend(scrape_youtube_videos_from_page(r.text))
                time.sleep(0.5)
            except:
                continue

        progress.close()

        seen = set()
        unique_ordered = []
        for vid in all_video_ids:
            if vid not in seen:
                unique_ordered.append(vid)
                seen.add(vid)

        log('Gesamt: %d Videos' % len(unique_ordered))
        return unique_ordered
    except:
        return []

def get_video_list(force_refresh=False):
    cached, cache_time = get_cached_videos()
    if not force_refresh and cached and (int(time.time()) - cache_time) < CACHE_VALIDITY:
        return cached
    videos = scrape_all_pages()
    if videos:
        save_cached_videos(videos)
    elif cached:
        return cached
    return videos

def get_latest_videos(force_refresh=False):
    cached, cache_time = get_cached_latest_videos()
    if not force_refresh and cached and (int(time.time()) - cache_time) < CACHE_VALIDITY:
        return cached
    videos = scrape_latest_page()
    if videos:
        save_cached_latest_videos(videos)
    elif cached:
        return cached
    return videos

def build_youtube_url(video_id):
    return 'plugin://plugin.video.youtube/play/?video_id=%s' % video_id

def show_main_menu():
    handle = int(sys.argv[1])
    items = [
        ('[B]Alle Videos (kompletter Thread)[/B]', 'all', 'DefaultMusicVideos.png', False),
        ('[B][COLOR lime]Neueste Videos (mit User + Titel)[/COLOR][/B]', 'latest', 'DefaultRecentlyAddedVideos.png', False),
        ('[COLOR yellow]Cache leeren[/COLOR]', 'clear_cache', 'DefaultAddonService.png', True)
    ]
    for label, mode, icon, is_action in items:
        item = xbmcgui.ListItem(label=label)
        item.setArt({'icon': icon})
        xbmcplugin.addDirectoryItem(handle, '%s?mode=%s' % (sys.argv[0], mode), item, not is_action)
    xbmcplugin.endOfDirectory(handle)

def list_all_videos(force_refresh=False):
    handle = int(sys.argv[1])
    video_ids = get_video_list(force_refresh)
    if not video_ids:
        xbmcplugin.endOfDirectory(handle, False)
        return

    item = xbmcgui.ListItem('[B][COLOR yellow]>>> Liste aktualisieren <<<[/COLOR][/B]')
    xbmcplugin.addDirectoryItem(handle, '%s?mode=all&refresh=1' % sys.argv[0], item, True)

    for idx, vid in enumerate(video_ids, 1):
        item = xbmcgui.ListItem('%d. Musik-Tipp (%s)' % (idx, vid))
        item.setProperty('IsPlayable', 'true')
        item.setArt({'thumb': 'https://img.youtube.com/vi/%s/hqdefault.jpg' % vid})
        xbmcplugin.addDirectoryItem(handle, build_youtube_url(vid), item, False)

    xbmcplugin.setContent(handle, 'videos')
    xbmcplugin.endOfDirectory(handle, True)
    xbmcgui.Dialog().notification('Kodinerds Musik-Tipps', '%d Videos' % len(video_ids), xbmcgui.NOTIFICATION_INFO, 2000)

def list_latest_videos(force_refresh=False):
    handle = int(sys.argv[1])
    videos_with_users = get_latest_videos(force_refresh)

    if not videos_with_users:
        xbmcgui.Dialog().ok('Kodinerds Musik-Tipps', 'Keine Videos gefunden.\nBitte Cache leeren und neu versuchen.')
        xbmcplugin.endOfDirectory(handle, False)
        return

    item = xbmcgui.ListItem('[B][COLOR yellow]>>> Neueste aktualisieren <<<[/COLOR][/B]')
    xbmcplugin.addDirectoryItem(handle, '%s?mode=latest&refresh=1' % sys.argv[0], item, True)

    # Video-IDs extrahieren fuer Metadaten-Abfrage
    video_ids = [v['video_id'] for v in videos_with_users]

    # Metadaten laden
    progress = xbmcgui.DialogProgress()
    progress.create('Kodinerds Musik-Tipps', 'Lade Video-Informationen...')
    metadata = get_video_metadata_batch(video_ids)
    progress.close()

    # Videos mit User + Titel/Interpret anzeigen
    for idx, video_data in enumerate(videos_with_users, 1):
        vid = video_data['video_id']
        username = video_data.get('username', 'Unbekannt')
        meta = metadata.get(vid, {'title': vid, 'author': 'YouTube'})

        # Format: "[User] Num. Interpret - Titel"
        display_title = '[COLOR yellow]%s[/COLOR] [COLOR lime]%d.[/COLOR] %s - %s' % (
            username, idx, meta['author'], meta['title']
        )

        item = xbmcgui.ListItem(display_title)
        item.setProperty('IsPlayable', 'true')

        item.setInfo('music', {
            'title': meta['title'],
            'artist': meta['author'],
            'comment': 'Vorgeschlagen von: %s' % username,
            'mediatype': 'song'
        })

        item.setArt({
            'thumb': 'https://img.youtube.com/vi/%s/hqdefault.jpg' % vid,
            'poster': 'https://img.youtube.com/vi/%s/hqdefault.jpg' % vid,
            'fanart': 'https://img.youtube.com/vi/%s/maxresdefault.jpg' % vid
        })

        xbmcplugin.addDirectoryItem(handle, build_youtube_url(vid), item, False)

    xbmcplugin.setContent(handle, 'songs')
    xbmcplugin.endOfDirectory(handle, True)
    xbmcgui.Dialog().notification('Kodinerds Musik-Tipps', '%d Videos mit User + Titel' % len(videos_with_users), xbmcgui.NOTIFICATION_INFO, 2000)

def clear_cache():
    for f in [CACHE_FILE, LATEST_CACHE_FILE, METADATA_CACHE_FILE]:
        if os.path.exists(f):
            os.remove(f)
    xbmcgui.Dialog().notification('Kodinerds Musik-Tipps', 'Cache geloescht!', xbmcgui.NOTIFICATION_INFO, 2000)

def router():
    params = parse_qs(sys.argv[2][1:])
    mode = params.get('mode', ['menu'])[0]
    force_refresh = params.get('refresh', ['0'])[0] == '1'
    if mode == 'all':
        list_all_videos(force_refresh)
    elif mode == 'latest':
        list_latest_videos(force_refresh)
    elif mode == 'clear_cache':
        clear_cache()
    else:
        show_main_menu()

if __name__ == '__main__':
    router()
