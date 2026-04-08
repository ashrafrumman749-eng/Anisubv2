import re
import time

def convert_vtt_to_srt(vtt_content):
    """Convert VTT subtitle to SRT format"""
    lines = vtt_content.strip().split('\n')
    result = []
    counter = 1
    i = 0
    
    # Skip WEBVTT header
    while i < len(lines) and (lines[i].startswith('WEBVTT') or lines[i].startswith('NOTE') or lines[i].strip() == ''):
        i += 1
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Skip NOTE blocks
        if line.startswith('NOTE'):
            while i < len(lines) and lines[i].strip() != '':
                i += 1
            i += 1
            continue
        
        # Timestamp line
        if '-->' in line:
            timestamp = line.replace('.', ',')
            # Remove positioning info
            timestamp = re.sub(r'\s+align:\S+|\s+position:\S+|\s+line:\S+|\s+size:\S+', '', timestamp)
            
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip() != '':
                text_lines.append(lines[i].strip())
                i += 1
            
            if text_lines:
                result.append(str(counter))
                result.append(timestamp)
                result.extend(text_lines)
                result.append('')
                counter += 1
        else:
            i += 1
    
    return '\n'.join(result)


def parse_srt(srt_text):
    """
    Parse SRT text into a list of dicts with 'times' and 'text'.
    This matches the format expected by app.py's srt_to_ass().
    """
    blocks = re.split(r'\n\s*\n', srt_text.strip())
    entries = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            # First line is index (skip), second line is timestamp, rest is text
            timestamp_line = lines[1].strip() if len(lines) > 1 else ''
            text_lines = lines[2:] if len(lines) > 2 else []
            # Some SRT may have index, timestamp, then text possibly multiline
            # Also handle case where first line might be timestamp if index missing?
            # Safer: find line containing '-->'
            time_line = None
            text_parts = []
            for line in lines:
                if '-->' in line:
                    time_line = line.strip()
                elif not re.match(r'^\d+$', line.strip()):  # not an index number
                    text_parts.append(line.strip())
            if time_line and text_parts:
                entries.append({
                    'times': time_line,
                    'text': '\n'.join(text_parts)
                })
    return entries


def parse_srt_blocks(srt_content):
    """Legacy: parse SRT into list of (index, timestamp, text) tuples"""
    blocks = re.split(r'\n\s*\n', srt_content.strip())
    parsed = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            try:
                idx = lines[0].strip()
                timestamp = lines[1].strip()
                text = ' '.join(lines[2:]).strip()
                if '-->' in timestamp and text:
                    parsed.append((idx, timestamp, text))
            except:
                pass
    return parsed


def translate_google(srt_content, dest_lang='bn'):
    """Translate SRT using Google Translate via deep-translator"""
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        return srt_content
    
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        return srt_content
    
    translator = GoogleTranslator(source='auto', target=dest_lang)
    result_lines = []
    batch_size = 20
    
    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i+batch_size]
        texts = [b[2] for b in batch]
        
        try:
            translated = translator.translate_batch(texts)
            for j, (idx, timestamp, _) in enumerate(batch):
                t = translated[j] if translated[j] else texts[j]
                result_lines.append(f"{idx}\n{timestamp}\n{t}\n")
        except Exception as e:
            # On error keep original
            for idx, timestamp, text in batch:
                result_lines.append(f"{idx}\n{timestamp}\n{text}\n")
        
        time.sleep(0.5)
    
    return '\n'.join(result_lines)


def translate_gemini(srt_content, api_key, dest_lang='bn'):
    """Translate SRT using Gemini AI"""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        blocks = parse_srt_blocks(srt_content)
        if not blocks:
            return srt_content
        
        lang_names = {'bn': 'Bengali (Bangla)', 'hi': 'Hindi', 'en': 'English'}
        lang_name = lang_names.get(dest_lang, 'Bengali')
        
        # Send all at once numbered
        numbered = '\n'.join([f"{i+1}. {b[2]}" for i, b in enumerate(blocks)])
        prompt = f"""Translate these subtitle lines to {lang_name}.
Return ONLY numbered translations, same format.
Keep the numbering. Do not add explanations.

{numbered}"""
        
        response = model.generate_content(prompt)
        resp_text = response.text.strip()
        
        # Parse numbered response
        trans_map = {}
        for line in resp_text.split('\n'):
            m = re.match(r'^(\d+)\.\s*(.+)$', line.strip())
            if m:
                trans_map[int(m.group(1))] = m.group(2).strip()
        
        result_lines = []
        for i, (idx, timestamp, text) in enumerate(blocks):
            translated = trans_map.get(i+1, text)
            result_lines.append(f"{idx}\n{timestamp}\n{translated}\n")
        
        return '\n'.join(result_lines)
        
    except Exception as e:
        # Fallback to Google Translate
        return translate_google(srt_content, dest_lang)


def translate_srt_text(srt_text, gemini_api_key=None, grok_api_key=None, batch_size=20):
    """
    Main translation function expected by app.py.
    Translates SRT text to Bengali using Gemini if API key provided,
    otherwise falls back to Google Translate.
    (grok_api_key is currently ignored but kept for compatibility)
    """
    dest_lang = 'bn'
    if gemini_api_key:
        return translate_gemini(srt_text, gemini_api_key, dest_lang)
    else:
        # Use Google Translate as default
        return translate_google(srt_text, dest_lang)
