import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request, send_file
try:
    from extractor import extract_sources
except ImportError:
    from extractor import extract_from_episode_page as extract_sources

try:
    from fb_uploader import upload_to_facebook
    FACEBOOK_AVAILABLE = True
except ImportError:
    FACEBOOK_AVAILABLE = False

    def upload_to_facebook(*args, **kwargs):
        raise RuntimeError("fb_uploader.py not found. Facebook upload is disabled.")
from translator import convert_vtt_to_srt, parse_srt, translate_srt_text
from uploader import upload_to_telegram


app = Flask(__name__)
TASKS = {}
BASE_DIR = Path(__file__).resolve().parent
TMP_DIR = Path('/tmp/anisubv2')
TMP_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_DIR = TMP_DIR / 'cookies'
COOKIE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR = TMP_DIR / 'outputs'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FONT_CANDIDATES = {
    'noto_sans_bn': {
        'display': 'Noto Sans Bengali',
        'family': 'Noto Sans Bengali',
        'paths': [
            '/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf',
            '/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf',
            '/app/fonts/NotoSansBengali-Regular.ttf',
        ],
    },
    'noto_serif_bn': {
        'display': 'Noto Serif Bengali',
        'family': 'Noto Serif Bengali',
        'paths': [
            '/usr/share/fonts/truetype/noto/NotoSerifBengali-Regular.ttf',
            '/usr/share/fonts/truetype/noto/NotoSerifBengali-Bold.ttf',
            '/app/fonts/NotoSerifBengali-Regular.ttf',
        ],
    },
    'lohit_bn': {
        'display': 'Lohit Bengali',
        'family': 'Lohit Bengali',
        'paths': [
            '/usr/share/fonts/truetype/lohit-bengali/Lohit-Bengali.ttf',
            '/app/fonts/Lohit-Bengali.ttf',
        ],
    },
    'kalpurush': {
        'display': 'Kalpurush',
        'family': 'Kalpurush',
        'paths': [
            '/app/fonts/Kalapurush.ttf',
        ],
    },
    'solaimanlipi': {
        'display': 'SolaimanLipi',
        'family': 'SolaimanLipi',
        'paths': [
            '/app/fonts/SolaimanLipi.ttf',
        ],
    },
}


def log(task, message, icon='ℹ️'):
    task['logs'].append({'ts': time.time(), 'icon': icon, 'message': message})


def ensure_fonts_dir():
    fonts_dir = TMP_DIR / 'fonts'
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for meta in FONT_CANDIDATES.values():
        for p in meta['paths']:
            if os.path.exists(p):
                dst = fonts_dir / Path(p).name
                if not dst.exists():
                    shutil.copy2(p, dst)
    return str(fonts_dir)


def get_font_family(font_key):
    return FONT_CANDIDATES.get(font_key, FONT_CANDIDATES['noto_sans_bn'])['family']


def pick_first(data, *keys, default=None):
    for key in keys:
        value = data.get(key)
        if value not in (None, '', []):
            return value
    return default


def ass_color(name):
    return {
        'white': '&H00FFFFFF',
        'yellow': '&H0000FFFF',
        'cyan': '&H00FFFF00',
    }.get((name or 'white').lower(), '&H00FFFFFF')


def ass_alignment(position):
    return {
        'bottom': 2,
        'middle': 5,
        'top': 8,
    }.get((position or 'bottom').lower(), 2)


def ass_background(bg):
    key = (bg or 'semi-transparent').lower()
    if key in ['none', 'transparent']:
        return 1, '&H00000000', 2, 0
    if key in ['black', 'black box', 'box']:
        return 3, '&HAA000000', 1, 0
    return 1, '&H80000000', 2, 0


def ffmpeg_escape_filter_path(path: str):
    path = path.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'").replace(',', '\\,')
    return path


def srt_time_to_ass(ts: str) -> str:
    return ts.replace(',', '.')


def srt_to_ass(srt_text, ass_path, font_key='noto_sans_bn', color='white',
               position='bottom', background='semi-transparent',
               bold=False, italic=False, font_size=42):
    items = parse_srt(srt_text)
    font_family = get_font_family(font_key)
    border_style, back_colour, outline, shadow = ass_background(background)
    alignment = ass_alignment(position)
    margin_v = {'bottom': 34, 'middle': 120, 'top': 34}.get((position or 'bottom').lower(), 34)

    header = (
        '[Script Info]\n'
        'Title: AniSub Bengali\n'
        'ScriptType: v4.00+\n'
        'PlayResX: 1280\n'
        'PlayResY: 720\n'
        'ScaledBorderAndShadow: yes\n'
        'WrapStyle: 2\n'
        'YCbCr Matrix: TV.709\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, '
        'Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,{font_family},{font_size},{ass_color(color)},&H0000FFFF,&H00111111,{back_colour},'
        f'{-1 if bold else 0},{-1 if italic else 0},0,0,100,100,0,0,{border_style},{outline},{shadow},'
        f'{alignment},60,60,{margin_v},1\n\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )

    dialogue_lines = []
    for item in items:
        parts = item['times'].replace('-->', '|').split('|')
        if len(parts) < 2:
            continue
        start, end = parts[0].strip(), parts[1].strip()
        text = item['text'].replace('\n', r'\N')
        text = re.sub(r'<[^>]+>', '', text)
        dialogue_lines.append(
            f'Dialogue: 0,{srt_time_to_ass(start)},{srt_time_to_ass(end)},Default,,0,0,0,,{text}'
        )

    content = header + '\n'.join(dialogue_lines) + '\n'
    Path(ass_path).write_text(content, encoding='utf-8')
    return ass_path


def download_text(url: str) -> str:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    response.encoding = response.encoding or 'utf-8'
    return response.text


def _parse_ffmpeg_time(line: str):
    m = re.search(r'time=(\d+):(\d+):([\d.]+)', line)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _get_duration(path_or_url: str):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path_or_url],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def run_ffmpeg_with_progress(cmd, task, duration=None, progress_start=50, progress_end=75):
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    for line in proc.stderr:
        line = line.rstrip()
        if not line:
            continue

        if any(k in line for k in ('frame=', 'fps=', 'time=', 'speed=', 'Error', 'error')):
            task['logs'].append({'ts': time.time(), 'icon': '🎞️', 'message': line})

        if duration and 'time=' in line:
            elapsed = _parse_ffmpeg_time(line)
            if elapsed is not None:
                ratio = min(elapsed / duration, 1.0)
                task['progress'] = int(progress_start + ratio * (progress_end - progress_start))

    proc.wait()
    return proc.returncode


def process_task(task_id, data):
    task = TASKS[task_id]
    task['status'] = 'Running'
    task['stage'] = 'extract'
    work_dir = OUTPUT_DIR / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        log(task, 'Task started', '🚀')
        source_url = pick_first(data, 'source_url')
        video_url = pick_first(data, 'video_url', 'iframe_url')
        subtitle_url = pick_first(data, 'subtitle_url', 'sub_url', 'sub_file_path')
        cookie_path = pick_first(data, 'cookie_path')

        # ── Extract ──────────────────────────────────────────────
        if video_url:
            log(task, f'Direct video: {video_url[:80]}...', '🔗')
        elif source_url:
            result = extract_sources(source_url, cookie_path=cookie_path)
            video_url = result.get('video_url') or result.get('m3u8_url')
            if not subtitle_url and result.get('subtitles'):
                subtitle_url = result['subtitles'][0]['url']
            task['extract_result'] = result
            log(task, f'Extracted video: {video_url}', '🔎')
            log(task, f'Extracted subtitle: {subtitle_url}', '📝')
        else:
            raise RuntimeError('Video URL বা Episode URL দাও')

        if not video_url:
            raise RuntimeError('No video URL found')

        # ── Download subtitle (optional) ────────────────────────
        srt_text = None
        ass_path_direct = None
        if subtitle_url:
            task['stage'] = 'download'
            task['progress'] = 10

            if subtitle_url.startswith('/'):
                subtitle_text = open(subtitle_url, encoding='utf-8').read()
                log(task, 'Subtitle loaded from uploaded file', '📂')
            else:
                subtitle_text = download_text(subtitle_url)
                log(task, 'Subtitle downloaded', '⬇️')

            if subtitle_url.lower().endswith('.ass') or subtitle_text.lstrip().startswith('[Script Info]'):
                ass_path_direct = str(work_dir / 'subtitle.ass')
                Path(ass_path_direct).write_text(subtitle_text, encoding='utf-8')
                log(task, 'ASS subtitle loaded directly', '🎨')
            elif subtitle_url.lower().endswith('.vtt') or subtitle_text.lstrip().startswith('WEBVTT'):
                srt_text = convert_vtt_to_srt(subtitle_text)
                log(task, 'VTT → SRT converted', '🔁')
            else:
                srt_text = subtitle_text

            if srt_text:
                original_srt_path = work_dir / 'original.srt'
                original_srt_path.write_text(srt_text, encoding='utf-8')
        else:
            log(task, 'No subtitle — video only mode', 'ℹ️')

        # ── Translate ────────────────────────────────────────────
        translated_srt = None
        if srt_text:
            task['stage'] = 'translate'
            task['progress'] = 30
            translate_to_bn = bool(data.get('translate_to_bn', data.get('sub_type') == 'translate'))
            if translate_to_bn:
                translated_srt = translate_srt_text(
                    srt_text,
                    gemini_api_key=data.get('gemini_api_key') or os.environ.get('GEMINI_API_KEY'),
                    grok_api_key=data.get('grok_api_key') or os.environ.get('XAI_API_KEY'),
                    batch_size=int(data.get('batch_size', 20)),
                )
                log(task, 'Subtitle translated to Bangla', '🇧🇩')
            else:
                translated_srt = srt_text
                log(task, 'Translation skipped', '⏭️')

            translated_srt_path = work_dir / 'translated.srt'
            translated_srt_path.write_text(translated_srt, encoding='utf-8')
            task['translated_srt_path'] = str(translated_srt_path)

        # ── ASS subtitle ─────────────────────────────────────────
        task['stage'] = 'process'
        task['progress'] = 50
        ass_path = ass_path_direct
        if translated_srt:
            ass_path = str(work_dir / 'subtitle.ass')
            srt_to_ass(
                translated_srt, ass_path,
                font_key=pick_first(data, 'font_family', 'font_name', default='noto_sans_bn'),
                color=pick_first(data, 'subtitle_color', 'color', default='white'),
                position=pick_first(data, 'subtitle_position', 'position', default='bottom'),
                background=pick_first(data, 'subtitle_background', 'bg', default='semi-transparent'),
                bold=bool(pick_first(data, 'subtitle_bold', 'bold', default=False)),
                italic=bool(pick_first(data, 'subtitle_italic', 'italic', default=False)),
                font_size=int(pick_first(data, 'subtitle_size', 'font_size', default=42)),
            )
            log(task, 'ASS subtitle prepared', '🎨')

        # ── Download video using N_m3u8DL-RE (with referer) ─────
        raw_video = str(work_dir / 'source.mp4')
        task['stage'] = 'download'
        task['progress'] = 40
        log(task, 'Downloading video...', '⬇️')

        # Check if N_m3u8DL-RE binary exists (from railway predeploy)
        n_m3u8dl_re_path = Path('./N_m3u8DL-RE')
        if not n_m3u8dl_re_path.exists():
            n_m3u8dl_re_path = Path('N_m3u8DL-RE')
        
        referer = data.get('source_url') or video_url
        
        if n_m3u8dl_re_path.exists():
            cmd = [
                str(n_m3u8dl_re_path), video_url,
                '-sv', 'best',
                '-mt',
                '-M', 'format=mp4',
                '-o', str(work_dir),
                '-H', f'Referer:{referer}'
            ]
            log(task, 'Using N_m3u8DL-RE for download...', '📥')
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0:
                # N_m3u8DL-RE saves as source.mp4 in work_dir
                downloaded_file = work_dir / 'source.mp4'
                if downloaded_file.exists() and downloaded_file.stat().st_size > 1024:
                    raw_video = str(downloaded_file)
                    log(task, 'Video downloaded via N_m3u8DL-RE', '✅')
                else:
                    raise RuntimeError('N_m3u8DL-RE output file missing or too small')
            else:
                raise RuntimeError(f'N_m3u8DL-RE failed: {proc.stderr}')
        else:
            # Fallback: requests with headers
            log(task, 'N_m3u8DL-RE not found, using requests fallback...', '⚠️')
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': referer
            }
            resp = requests.get(video_url, headers=headers, stream=True, timeout=60)
            resp.raise_for_status()
            with open(raw_video, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.getsize(raw_video) < 1024:
                raise RuntimeError('Downloaded file too small (requests fallback)')
            log(task, 'Video downloaded via requests', '✅')

        # ── ffmpeg render ───────────────────────────────────────
        final_video_path = str(work_dir / 'final.mp4')
        fonts_dir = ensure_fonts_dir()

        if ass_path:
            vf_filter = (
                f"scale=1280:-2,"
                f"ass='{ffmpeg_escape_filter_path(ass_path)}':"
                f"fontsdir='{ffmpeg_escape_filter_path(fonts_dir)}'"
            )
        else:
            vf_filter = 'scale=1280:-2'

        cmd = [
            'ffmpeg', '-y',
            '-i', raw_video,
            '-vf', vf_filter,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            final_video_path,
        ]

        log(task, 'Rendering video...', '🎬')
        duration = _get_duration(raw_video)
        returncode = run_ffmpeg_with_progress(cmd, task, duration=duration)

        if returncode != 0 or not os.path.exists(final_video_path):
            raise RuntimeError(f'ffmpeg failed with code {returncode}')

        log(task, 'Video render complete', '✅')
        task['progress'] = 75
        task['final_video_path'] = final_video_path
        task['ass_path'] = ass_path

        # ── Upload ──────────────────────────────────────────────
        task['stage'] = 'upload'
        upload_targets = data.get('upload_targets') or ['telegram']

        if 'telegram' in upload_targets:
            log(task, 'Uploading to Telegram...', '☁️')

            def tg_prog(pct):
                task['progress'] = 75 + int(pct * 0.15)
                if pct % 20 == 0:
                    log(task, f'Telegram upload: {pct}%', '📤')

            tg_link = upload_to_telegram(
                final_video_path,
                data.get('tg_title', 'AniSub Video'),
                data.get('tg_caption', ''),
                tg_prog,
            )
            task['tg_link'] = tg_link
            task['post_link'] = tg_link
            log(task, f'Telegram done: {tg_link}', '✅')

        if 'facebook' in upload_targets:
            fb_page_id = data.get('fb_page_id') or os.environ.get('FB_PAGE_ID')
            fb_token = data.get('fb_token') or os.environ.get('FB_PAGE_TOKEN')
            if not fb_page_id or not fb_token:
                log(task, 'Facebook credentials missing, skipping', '⚠️')
            else:
                log(task, 'Uploading to Facebook...', '📘')

                def fb_prog(pct):
                    task['progress'] = 90 + int(pct * 0.10)
                    if pct % 20 == 0:
                        log(task, f'Facebook upload: {pct}%', '📤')

                fb_link = upload_to_facebook(
                    final_video_path,
                    data.get('tg_title', 'AniSub Video'),
                    data.get('tg_caption', ''),
                    fb_page_id, fb_token, fb_prog,
                )
                task['fb_link'] = fb_link
                task['post_link'] = fb_link
                log(task, f'Facebook done: {fb_link}', '✅')

        task['stage'] = 'done'
        task['progress'] = 100
        task['status'] = 'Done'
        log(task, 'All done!', '🏁')

    except Exception as exc:
        task['status'] = 'Error'
        task['stage'] = 'error'
        task['error'] = str(exc)
        log(task, f'Error: {exc}', '❌')


@app.route('/')
def index():
    return render_template('index.html', fonts=FONT_CANDIDATES)


@app.route('/extract', methods=['POST'])
def extract_route():
    payload = request.get_json(force=True)
    result = extract_sources(payload['url'], cookie_path=payload.get('cookie_path'))
    return jsonify(result)


@app.route('/start', methods=['POST'])
def start_route():
    payload = request.get_json(force=True)
    task_id = uuid.uuid4().hex[:12]
    TASKS[task_id] = {
        'id': task_id,
        'status': 'Queued',
        'stage': 'queued',
        'progress': 0,
        'logs': [],
    }
    threading.Thread(target=process_task, args=(task_id, payload), daemon=True).start()
    return jsonify({'task_id': task_id, 'status': 'Queued'})


@app.route('/status/<task_id>')
def status_route(task_id):
    task = TASKS.get(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    offset = int(request.args.get('log_offset', request.args.get('offset', 0)))
    return jsonify({
        'id': task['id'],
        'status': task['status'],
        'stage': task['stage'],
        'progress': task['progress'],
        'logs': task['logs'][offset:],
        'log_total': len(task['logs']),
        'tg_link': task.get('tg_link'),
        'fb_link': task.get('fb_link'),
        'post_link': task.get('post_link'),
        'error': task.get('error'),
        'final_video_path': task.get('final_video_path'),
        'has_preview': bool(task.get('final_video_path') and os.path.exists(task['final_video_path'])),
    })


@app.route('/preview/<task_id>')
def preview_route(task_id):
    task = TASKS.get(task_id)
    if not task or not task.get('final_video_path') or not os.path.exists(task['final_video_path']):
        return jsonify({'error': 'Preview not ready'}), 404
    return send_file(task['final_video_path'], mimetype='video/mp4')


@app.route('/upload_subtitle', methods=['POST'])
def upload_subtitle_route():
    if 'subtitle_file' not in request.files:
        return jsonify({'error': 'No subtitle_file uploaded'}), 400
    file = request.files['subtitle_file']
    dest = TMP_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    file.save(dest)
    return jsonify({'ok': True, 'subtitle_path': str(dest)})


@app.route('/upload_subtitle_text', methods=['POST'])
def upload_subtitle_text_route():
    data = request.get_json(force=True)
    filename = data.get('filename', 'subtitle.srt')
    content = data.get('content', '')
    if not content:
        return jsonify({'error': 'No content'}), 400
    dest = TMP_DIR / f"{uuid.uuid4().hex[:8]}_{filename}"
    dest.write_text(content, encoding='utf-8')
    return jsonify({'ok': True, 'subtitle_path': str(dest)})


@app.route('/upload_cookie', methods=['POST'])
def upload_cookie_route():
    if 'cookie_file' not in request.files:
        return jsonify({'error': 'No cookie_file uploaded'}), 400
    file = request.files['cookie_file']
    dest = COOKIE_DIR / file.filename
    file.save(dest)
    return jsonify({'ok': True, 'cookie_path': str(dest)})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=True)
