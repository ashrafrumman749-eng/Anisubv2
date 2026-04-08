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
    
    # 📌 ১. সাইট স্পেসিফিক হ্যান্ডলিং: dramacool / watchasia
    if 'dramacool' in url.lower() or 'watchasia' in url.lower():
        try:
            import dramacool as dc
            # `dramacool` প্যাকেজ ডাউনলোড লিংক বের করে দেবে
            download_links = dc.get_download_links(url)
            if download_links and download_links.get('m3u8'):
                result['m3u8_url'] = download_links['m3u8']
                if download_links.get('subtitles'):
                    result['subtitles'] = [{'url': sub, 'lang': 'en'} for sub in download_links['subtitles']]
                return result
        except ImportError:
            result['errors'].append("dramacool package not installed, falling back to manual extraction.")
        except Exception as e:
            result['errors'].append(f"dramacool package error: {e}")
    
    # ২. ব্যাকআপ পদ্ধতি: Consumet API
    if 'dramacool' in url.lower() or 'watchasia' in url.lower():
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

    # 📌 ৩. ক্লাউডফ্লেয়ার বাইপাস + regex (সবশেষ চেষ্টা)
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
    
    for pattern in m3u8_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        for match in matches:
            if is_valid_m3u8(match):
                result['m3u8_url'] = match
                break
        if result['m3u8_url']:
            break
    
    # বাকি কোড (unpack_js, iframe, yt-dlp ইত্যাদি) আগের মতো থাকবে... 

    return result

def is_valid_m3u8(url):
    if not url or not isinstance(url, str):
        return False
    if not url.startswith('http'):
        return False
    if '.m3u8' not in url.lower():
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
