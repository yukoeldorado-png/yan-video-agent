import os, re, time, glob, json, asyncio, subprocess, shutil, edge_tts
from google import genai
from google.genai import types

GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
DEFAULT_SUB = "YaNstudio AI"
DEFAULT_CRE = ""

BODY_TEXT = os.environ.get("RELEASE_BODY", "")
cre_match = re.search(r'(?i)cre\s*[:=]\s*(.+)', BODY_TEXT)
sub_match = re.search(r'(?i)sub\s*[:=]\s*(.+)', BODY_TEXT)

CUSTOM_CRE = cre_match.group(1).strip() if cre_match else DEFAULT_CRE
CUSTOM_SUB = sub_match.group(1).strip() if sub_match else DEFAULT_SUB

client = genai.Client(api_key=GEMINI_KEY)

YAN_PROMPT = """
Persona: YaN - Chuyên gia Biên dịch ngôn ngữ & Bản địa hóa hoạt hình Trung Quốc.
TỐI HẬU THƯ:
1. Dịch TOÀN BỘ video từ 00:00 đến hết video. Tuyệt đối KHÔNG bỏ dở giữa chừng.
2. CHỈ dịch lời thoại do nhân vật nói ra mồm. TUYỆT ĐỐI BỎ QUA các bảng thông báo hệ thống, bảng thuộc tính chữ dài.
3. Mỗi câu thoại chỉ nằm trên 1 DÒNG ĐƠN (tối đa 10-12 từ).
4. Định dạng chuẩn: 00:mm:ss,mmm --> 00:mm:ss,mmm. CẤM DẤU NGOẶC KÉP. Không xuất lời dẫn.
"""

CJK_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
if not os.path.exists(CJK_FONT):
    CJK_FONT = "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"

capcut_client = None
try:
    from capcut_tts_api import CapCutClient
    capcut_client = CapCutClient(timeout=45)
except: pass

def get_video_dimensions(p):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", p]
    try: return int(json.loads(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout)["streams"][0]["width"]), int(json.loads(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout)["streams"][0]["height"])
    except: return 1920, 1080

def get_audio_duration(p):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p], stdout=subprocess.PIPE, text=True).stdout.strip())
    except: return 2.0

def clean_time(t):
    t = t.strip().replace('.', ',')
    parts = t.split(':')
    if len(parts) == 2:
        s = parts[1] if ',' in parts[1] else parts[1] + ',000'
        return f"00:{int(parts[0]):02d}:{s}"
    elif len(parts) == 3:
        s = parts[2] if ',' in parts[2] else parts[2] + ',000'
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}:{s}"
    return t

def rebuild_srt(raw):
    raw = raw.replace('：', ':').replace('，', ',').replace('—>', '-->').replace('->', '-->')
    ts_pattern = r'((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,\.]\d{1,3})?)\s*-->\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,\.]\d{1,3})?)'
    matches = list(re.finditer(ts_pattern, raw))
    if not matches: return raw
    entries = []
    for i in range(len(matches)):
        st, et = clean_time(matches[i].group(1)), clean_time(matches[i].group(2))
        p1 = matches[i].end()
        p2 = matches[i+1].start() if i+1 < len(matches) else len(raw)
        lines = [l.strip() for l in raw[p1:p2].split('\n') if l.strip()]
        valid = [l for l in lines if not re.match(r'^\d+$', l) and '-->' not in l]
        txt = " ".join(valid).replace('"', '').strip()
        if txt and len(txt.split()) < 35:
            entries.append((st, et, txt))
    return "\n\n".join([f"{idx}\n{s} --> {e}\n{t}" for idx, (s, e, t) in enumerate(entries, 1)])

def parse_srt(p):
    items = []
    with open(p, "r", encoding="utf-8") as f: content = f.read()
    for m in re.finditer(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)', content, re.DOTALL):
        h1, mn1, s1 = m.group(2).split(':'); s1, ms1 = s1.split(',')
        h2, mn2, s2 = m.group(3).split(':'); s2, ms2 = s2.split(',')
        items.append((int(h1)*3600+int(mn1)*60+int(s1)+int(ms1)/1000.0, int(h2)*3600+int(mn2)*60+int(s2)+int(ms2)/1000.0, m.group(4).strip().replace('\n', ' ')))
    return items

def generate_voice(text, out_p):
    if capcut_client:
        for _ in range(5):
            try:
                res = capcut_client.generate_speech(text, voice="BV421_vivn_streaming", timeout=30)
                capcut_client.download_tts_audio(res, out_p)
                if os.path.exists(out_p) and os.path.getsize(out_p) > 0: return True
            except: time.sleep(1.5)
    try:
        asyncio.run(edge_tts.Communicate(text, "vi-VN-HoaiMyNeural", pitch="+16Hz", rate="+8%").save(out_p))
        return os.path.exists(out_p) and os.path.getsize(out_p) > 0
    except: return False

def process_single_video(video_path, tag_name):
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    ep_display = base_name.replace("_", " ").replace("-", " ")
    print(f"\n[*] ĐANG XỬ LÝ: {base_name} | Cre: {CUSTOM_CRE or 'Ẩn'} | Sub: {CUSTOM_SUB}")

    clean_vid = f"clean_{base_name}.mp4"
    shutil.copyfile(video_path, clean_vid)

    v_file = client.files.upload(file=clean_vid)
    while v_file.state.name == "PROCESSING":
        time.sleep(1.5)
        v_file = client.files.get(name=v_file.name)

    config = types.GenerateContentConfig(system_instruction=YAN_PROMPT, temperature=0.1)
    resp = None
    for m in ["gemini-3.7-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            print(f"[*] Đang dịch với model {m}...")
            r = client.models.generate_content(model=m, contents=[v_file, "Dịch toàn bộ video sang tiếng Việt."], config=config)
            if r and r.text: resp = r; break
        except Exception as e:
            print(f"[!] {m} bận: {e}")
            time.sleep(1.5)
            
    try: client.files.delete(name=v_file.name)
    except: pass
    
    if not resp or not resp.text:
        raise Exception("Không thể lấy phụ đề từ Gemini!")

    srt_file = f"sub_{base_name}.srt"
    with open(srt_file, "w", encoding="utf-8") as f: f.write(rebuild_srt(resp.text))

    items = parse_srt(srt_file)
    inputs, parts = [], []
    temp_dir = f"temp_tts_{base_name}"
    os.makedirs(temp_dir, exist_ok=True)
    idx = 0
    for s, e, txt in items:
        if not txt or txt.startswith("["): continue
        p_raw, p_fit = f"{temp_dir}/r_{idx}.mp3", f"{temp_dir}/f_{idx}.mp3"
        if generate_voice(txt, p_raw):
            dur = get_audio_duration(p_raw)
            max_d = max(0.5, e - s - 0.08)
            chosen_p = p_raw
            if dur > max_d:
                subprocess.run(["ffmpeg", "-y", "-i", p_raw, "-filter:a", f"atempo={min(1.85, dur/max_d):.2f}", "-vn", p_fit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                chosen_p = p_fit
            inputs.extend(["-i", chosen_p])
            d_ms = int(s * 1000)
            parts.append(f"[{idx}]adelay={d_ms}|{d_ms}[a{idx}];")
            idx += 1

    tts_audio = f"tts_{base_name}.wav"
    subprocess.run(["ffmpeg", "-y"] + inputs + ["-filter_complex", "".join(parts) + f"{''.join([f'[a{i}]' for i in range(idx)])}amix=inputs={idx}:dropout_transition=0:normalize=0[out]", "-map", "[out]", tts_audio], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    w, h = get_video_dimensions(clean_vid)
    y1_pct, y2_pct = 0.93, 1.00
    y_start = (int(h * y1_pct) // 2) * 2
    crop_h = max(24, min((int(h * (y2_pct - y1_pct)) // 2) * 2, h - y_start))
    center_margin_v = max(4, int((1.0 - (y1_pct + y2_pct)/2.0) * 288 - 4))

    cre_cmd = f"drawtext=text='cre - {CUSTOM_CRE}':x=25:y=20:fontsize=16:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1," if (CUSTOM_CRE and CUSTOM_CRE != "????") else ""
    t_sub = CUSTOM_SUB.replace("'", "").replace(":", " -")
    en_str = "+".join([f"between(t,{max(0, s-0.08):.2f},{e+0.18:.2f})" for s, e, t in items if t]) or "1"

    vf = (
        f"[0:v]split[m][c];"
        f"[c]crop=iw:{crop_h}:0:{y_start},gblur=sigma=15[b];"
        f"[m][b]overlay=0:{y_start}:enable='{en_str}'[v1];"
        f"[v1]{cre_cmd}"
        f"drawtext=text='sub - {t_sub}':x=25:y=42:fontsize=16:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='{ep_display}':x=w-tw-30:y=20:fontsize=22:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='{t_sub}':x='mod(t*65,w+tw)-tw':y='mod(t*32,h+th)-th':fontsize=20:fontcolor=white@0.10,"
        f"subtitles=filename='{srt_file}':force_style='FontName=Comfortaa,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.0,Alignment=2,MarginV={center_margin_v}'[vout]"
    )
    af = "[0:a]volume=0.15[bg];[1:a]volume=1.0[voice];[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]"

    output_filename = f"Video_HoanThanh_{base_name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", clean_vid, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", output_filename], check=True)

    print(f"[✓] Đã render xong: {output_filename}")
    if tag_name:
        subprocess.run(["gh", "release", "upload", tag_name, output_filename, "--clobber"])
        print(f"[✓] Đã upload lên Release: {tag_name}")
        
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    files = glob.glob("input_dir/*.*")
    video_files = sorted([f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv')) and "video_hoanthanh" not in f.lower()])
    if not video_files: raise Exception("Không tìm thấy file video nào trong Release!")

    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        tag = subprocess.run(["gh", "release", "view", "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True).stdout.strip()
        
    print(f"[*] Tìm thấy {len(video_files)} video. Bắt đầu xử lý...")
    for vid in video_files:
        process_single_video(vid, tag)

if __name__ == "__main__":
    main()
