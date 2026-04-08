import cloudscraper
from bs4 import BeautifulSoup
import re
import json
import base64
import shutil
import subprocess
import urllib.parse
import os
import requests

def extract_from_episode_page(url, cookie_path=None):
    result = {
        'm3u8_url': None,
        'subtitles': [],
        'iframe_urls': [],
        'errors': []
    }
    
    # ১. Dramacool / Watchasia সাইটের জন্য Consumet API (সবচেয়ে নির্ভরযোগ্য)
    if 'dramacool' in url.lower() or 'watchasia' in url.lower():
        # URL থেকে episode আইডি বের করো (যেমন: /episode/108612/...)
        episode_match = re.search(r'/episode/(\d+)/', url)
        if episode_match:
            episode_id = episode_match.group(1)
            try:
                api_url = f"https://api.consumet.org/movies/dramacool/watch?episodeId={episode_id}"
                resp = requests.get(api_url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    sources = data.get('sources', [])
                    for src in sources:
                        if src.get('url') and '.m3u8' in src['url']:
                            result['m3u8_url'] = src['url']
                            break
                    # সাবটাইটেল থাকলে নাও
                    for sub in data.get('subtitles', []):
                        if sub.get('url'):
                            result['subtitles'].append({
                                'url': sub['url'],
                                'lang': sub.get('lang', 'en')
                            })
                    if result['m3u8_url']:
                        return result
            except Exception as e:
                result['errors'].append(f"Consumet API error: {e}")
    
    # ২. পুরনো পদ্ধতি (cloudscraper + regex) – ব্যাকআপ
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False}
    )
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        res = scraper.get(url, timeout=20, headers=headers)
        res.raise_for_status()
        html = res.text
    except Exception as e:
        result['errors'].append(f"Main page fetch failed: {e}")
        return result
        
    soup = BeautifulSoup(html, 'lxml')
    
    # m3u8 প্যাটার্নস (বিস্তৃত)
    m3u8_patterns = [
        r'https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*',
        r'file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'src["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'url["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'source["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'video["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'link["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hls["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hlsUrl["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'hls_url["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'videoUrl["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'video_url["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'videoSrc["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'video_src["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'stream["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'streamUrl["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'stream_url["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'player["\']?\s*:\s*\{[^}]*["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'jwplayer[^;]*file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'plyr["\']?\s*:\s*[^}]*["\']?src["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'data-video["\']?\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'data-src["\']?\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'data-url["\']?\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'embed["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'iframe["\']?[^>]*src["\']?\s*=\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'master["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'playlist["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'server["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'serverUrl["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'episode["\']?[^}]*["\']?file["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        r'quality[^}]*["\']?url["\']?\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
    ]
    
    # মেইন HTML এ খোঁজো
    for pattern in m3u8_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            if is_valid_m3u8(match):
                result['m3u8_url'] = match
                break
        if result['m3u8_url']:
            break
    
    # আনপ্যাকড জাভাস্ক্রিপ্টে খোঁজো
    if not result['m3u8_url']:
        unpacked = unpack_js(html)
        if unpacked:
            for pattern in m3u8_patterns:
                matches = re.findall(pattern, unpacked, re.IGNORECASE)
                for match in matches:
                    if is_valid_m3u8(match):
                        result['m3u8_url'] = match
                        break
                if result['m3u8_url']:
                    break
    
    # স্ক্রিপ্ট ট্যাগে খোঁজো
    if not result['m3u8_url']:
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                for pattern in m3u8_patterns:
                    matches = re.findall(pattern, script.string, re.IGNORECASE)
                    for match in matches:
                        if is_valid_m3u8(match):
                            result['m3u8_url'] = match
                            break
                    if result['m3u8_url']:
                        break
                if result['m3u8_url']:
                    break
    
    # JSON ডাটা খোঁজো
    if not result['m3u8_url']:
        json_patterns = [
            r'var\s+\w+\s*=\s*(\{[^;]*"(?:file|src|url|video|stream)"[^}]*\})',
            r'window\.\w+\s*=\s*(\{[^;]*"(?:file|src|url|video|stream)"[^}]*\})',
            r'const\s+\w+\s*=\s*(\{[^;]*"(?:file|src|url|video|stream)"[^}]*\})',
        ]
        for pattern in json_patterns:
            matches = re.findall(pattern, html)
            for match in matches:
                try:
                    data = json.loads(match)
                    for key in ['file', 'src', 'url', 'video', 'stream', 'hls', 'source']:
                        if key in data and is_valid_m3u8(data[key]):
                            result['m3u8_url'] = data[key]
                            break
                    if result['m3u8_url']:
                        break
                except:
                    pass
            if result['m3u8_url']:
                break
    
    # সাবটাইটেল এক্সট্র্যাক্ট
    result['subtitles'].extend(extract_subtitles(html))
    
    # আইফ্রেম খোঁজো
    iframes = soup.find_all('iframe')
    for iframe in iframes:
        src = iframe.get('src') or iframe.get('data-src') or iframe.get('data-lazy-src')
        if src:
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                parsed = urllib.parse.urlparse(url)
                src = f"{parsed.scheme}://{parsed.netloc}{src}"
            if src.startswith('http') and src not in result['iframe_urls']:
                result['iframe_urls'].append(src)
    
    iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for src in iframe_matches:
        if src.startswith('//'):
            src = 'https:' + src
        elif src.startswith('/'):
            parsed = urllib.parse.urlparse(url)
            src = f"{parsed.scheme}://{parsed.netloc}{src}"
        if src.startswith('http') and src not in result['iframe_urls']:
            result['iframe_urls'].append(src)
    
    # আইফ্রেমের ভিতরে খোঁজো
    if not result['m3u8_url'] and result['iframe_urls']:
        for iframe_url in result['iframe_urls']:
            try:
                if_res = scraper.get(iframe_url, timeout=15, headers=headers)
                if_html = if_res.text
                
                # বেস৬৪ ডিকোড চেষ্টা
                b64_patterns = [
                    r'atob\([\'"]([^(\'"]+)[\'"]\)',
                    r'base64["\']?\s*:\s*["\']([A-Za-z0-9+/=]+)["\']',
                    r'["\']([A-Za-z0-9+/=]{50,})["\']',
                ]
                for pattern in b64_patterns:
                    b64_matches = re.findall(pattern, if_html)
                    for b64 in b64_matches:
                        try:
                            decoded = base64.b64decode(b64).decode('utf-8')
                            if '.m3u8' in decoded:
                                m3u8_matches = re.findall(r'https?://[^"\'\s<>]+\.m3u8[^"\'\s<>]*', decoded)
                                for m in m3u8_matches:
                                    if is_valid_m3u8(m):
                                        result['m3u8_url'] = m
                                        break
                                if result['m3u8_url']:
                                    break
                        except:
                            pass
                    if result['m3u8_url']:
                        break
                
                # সরাসরি প্যাটার্ন ম্যাচ
                if not result['m3u8_url']:
                    for pattern in m3u8_patterns:
                        matches = re.findall(pattern, if_html, re.IGNORECASE)
                        for match in matches:
                            if is_valid_m3u8(match):
                                result['m3u8_url'] = match
                                break
                        if result['m3u8_url']:
                            break
                
                # আনপ্যাকড জেএস
                if not result['m3u8_url']:
                    unpacked = unpack_js(if_html)
                    if unpacked:
                        for pattern in m3u8_patterns:
                            matches = re.findall(pattern, unpacked, re.IGNORECASE)
                            for match in matches:
                                if is_valid_m3u8(match):
                                    result['m3u8_url'] = match
                                    break
                            if result['m3u8_url']:
                                break
                
                result['subtitles'].extend(extract_subtitles(if_html))
                if result['m3u8_url']:
                    break
            except Exception as e:
                result['errors'].append(f"Iframe fetch failed ({iframe_url}): {e}")
    
    # yt-dlp ফলব্যাক
    if not result['m3u8_url']:
        if shutil.which('yt-dlp') is not None:
            cmd = ['yt-dlp', '--dump-json', '--no-download', url]
            if cookie_path and os.path.exists(cookie_path):
                cmd.extend(['--cookies', cookie_path])
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if proc.returncode == 0:
                    lines = proc.stdout.strip().split('\n')
                    for line in lines:
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            if data.get('url') and is_valid_m3u8(data['url']):
                                result['m3u8_url'] = data['url']
                                break
                            elif data.get('formats'):
                                for f in reversed(data['formats']):
                                    if f.get('url') and is_valid_m3u8(f['url']):
                                        result['m3u8_url'] = f['url']
                                        break
                                if result['m3u8_url']:
                                    break
                        except:
                            continue
            except Exception as e:
                result['errors'].append(f"yt-dlp fallback failed: {e}")
    
    # ডুপ্লিকেট সাবটাইটেল রিমুভ
    seen = set()
    unique_subs = []
    for s in result['subtitles']:
        if s['url'] not in seen:
            seen.add(s['url'])
            unique_subs.append(s)
    result['subtitles'] = unique_subs
    
    return result


def is_valid_m3u8(url):
    if not url or not isinstance(url, str):
        return False
    if not url.startswith('http'):
        return False
    if '.m3u8' not in url.lower():
        return False
    invalid_exts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico', '.bmp', '.css', '.js']
    lower_url = url.lower()
    if any(lower_url.endswith(ext) for ext in invalid_exts):
        return False
    invalid_patterns = ['thumbnail', 'thumb', 'poster', 'preview', 'banner', 'logo', 'icon']
    if any(p in lower_url for p in invalid_patterns):
        return False
    return True


def unpack_js(html):
    patterns = [
        r'eval\((function\(p,a,c,k,e,?[rd]?\).*?)\)',
        r'eval\((function\(p,a,c,k,e,d\).*?)\)',
        r'eval\((function\(p,a,c,k,e,r\).*?)\)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            return match.group(1)
    return ""


def extract_subtitles(html):
    subs = []
    patterns = [
        r'https?://[^"\'\s<>]+\.(?:srt|vtt|ass)(?:\?[^"\'\s<>]*)?',
        r'subtitle["\']?\s*:\s*["\'](http[^"\']+\.(?:srt|vtt|ass))["\']',
        r'subtitles["\']?\s*:\s*["\'](http[^"\']+\.(?:srt|vtt|ass))["\']',
        r'sub["\']?\s*:\s*["\'](http[^"\']+\.(?:srt|vtt|ass))["\']',
        r'track["\']?\s*:\s*["\'](http[^"\']+\.(?:srt|vtt|ass))["\']',
        r'captions["\']?\s*:\s*["\'](http[^"\']+\.(?:srt|vtt|ass))["\']',
        r'kind["\']?\s*:\s*["\']subtitles["\'][^}]*["\']?src["\']?\s*:\s*["\'](http[^"\']+)["\']',
    ]
    try:
        soup = BeautifulSoup(html, 'lxml')
        for track in soup.find_all('track'):
            if track.get('kind') in ['subtitles', 'captions']:
                src = track.get('src')
                if src and src.startswith('http'):
                    subs.append({'url': src, 'lang': detect_lang(src, track.get('srclang', ''))})
    except:
        pass
    
    for p in patterns:
        for match in re.findall(p, html, re.IGNORECASE):
            if match.startswith('http') and {'url': match, 'lang': detect_lang(match, '')} not in subs:
                subs.append({'url': match, 'lang': detect_lang(match, '')})
    
    json_patterns = [
        r'["\']?subtitles?["\']?\s*:\s*(\[[^\]]+\])',
        r'["\']?tracks?["\']?\s*:\s*(\[[^\]]+\])',
    ]
    for pattern in json_patterns:
        matches = re.findall(pattern, html)
        for match in matches:
            try:
                data = json.loads(match)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            src = item.get('src') or item.get('file') or item.get('url')
                            if src and src.startswith('http'):
                                lang = item.get('srclang') or item.get('lang') or item.get('language', '')
                                subs.append({'url': src, 'lang': detect_lang(src, lang)})
            except:
                pass
    return subs


def detect_lang(url, srclang):
    s = f"{url} {srclang}".lower()
    if any(x in s for x in ['bn', 'bangla', 'bengali', 'বাং']):
        return 'bn'
    if any(x in s for x in ['en', 'english', 'eng']):
        return 'en'
    if any(x in s for x in ['hi', 'hindi', 'हिंदी']):
        return 'hi'
    if any(x in s for x in ['ja', 'jp', 'japanese', '日本語']):
        return 'ja'
    if any(x in s for x in ['ko', 'kr', 'korean', '한국어']):
        return 'ko'
    return 'en'
