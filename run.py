import os, re, time, glob, json, asyncio, subprocess, shutil, edge_tts
from google import genai
from google.genai import types

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
EPISODE    = os.environ.get("EPISODE", "Tập 1").strip()
CRE_NAME   = "????"
SUB_NAME   = "Ynvo IYaN (sub)"

client = genai.Client(api_key=GEMINI_KEY)

YAN_PROMPT = """
Persona: YaN - Chuyên gia Biên dịch ngôn ngữ & Bản địa hóa hoạt hình Trung Quốc.
TỐI HẬU THƯ:
1. Mỗi entry phụ đề BẮT BUỘC chỉ nằm trên DUY NHẤT 1 DÒNG ĐƠN (tối đa 10-12 từ). Nếu câu dài, BẮT BUỘC ngắt làm 2 entry riêng.
2. Định dạng: 00:mm:ss,mmm --> 00:mm:ss,mmm. CẤM DẤU NGOẶC KÉP. Không xuất lời dẫn.
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
    try:
        data = json.loads(subprocess.run(cmd, stdout=subprocess.PIPE, text=True).stdout)["streams"][0]
        return int(data["width"]), int(data["height"])
    except: return 1920, 1080

def get_audio_duration(p):
    try: return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", p], stdout=subprocess.PIPE, text=True).stdout.strip())
    except: return 2.0

def clean_time(t):
    t = t.strip().replace('.', ',').split(':')
    return f"00:{int(t[0]):02d}:{t[1]}" if len(t)==2 else (f"{int(t[0]):02d}:{int(t[1]):02d}:{t[2]}" if len(t)==3 else ':'.join(t))

def rebuild_srt(raw):
    matches = list(re.finditer(r'(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}|\d{1,2}:\d{2}[,\.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,\.]\d{1,3}|\d{1,2}:\d{2}[,\.]\d{1,3})', raw))
    if not matches: return raw
    entries = []
    for i in range(len(matches)):
        st, et = clean_time(matches[i].group(1)), clean_time(matches[i].group(2))
        lines = [l.strip() for l in raw[matches[i].end():(matches[i+1].start() if i+1<len(matches) else len(raw))].split('\n') if l.strip()]
        txt = " ".join([l for l in lines if not re.match(r'^\d+$', l) and '-->' not in l]).replace('"', '').strip()
        if txt: entries.append((st, et, txt))
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

def main():
    files = glob.glob("input_dir/*.*")
    video_files = [f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv'))]
    if not video_files:
        raise Exception("Không tìm thấy file video nào được tải lên!")
    
    raw_video = "raw_clean.mp4"
    shutil.copyfile(video_files[0], raw_video)
    print(f"[*] Đã nhận diện video: {video_files[0]}")
    
    print("[*] Đẩy video lên Google Gemini...")
    v_file = client.files.upload(file=raw_video)
    while v_file.state.name == "PROCESSING":
        time.sleep(1.5)
        v_file = client.files.get(name=v_file.name)
    
    print("[*] Bóc sub AI với Gemini 3.7 / 2.0 Flash...")
    config = types.GenerateContentConfig(system_instruction=YAN_PROMPT, temperature=0.1)
    resp = None
    for m in ["gemini-3.7-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
        try:
            r = client.models.generate_content(model=m, contents=[v_file, "Xuất SRT chuẩn 1 dòng."], config=config)
            if r and r.text: resp = r; break
        except: time.sleep(1.5)
    try: client.files.delete(name=v_file.name)
    except: pass
    
    srt_file = "sub.srt"
    with open(srt_file, "w", encoding="utf-8") as f: f.write(rebuild_srt(resp.text))
    
    # Tọa độ ôm trọn sub gốc sát đáy
    y1_pct, y2_pct = 0.93, 1.00
    
    print("[*] Lồng tiếng CapCut Nguồn Nhỏ (BV421)...")
    items = parse_srt(srt_file)
    inputs, parts = [], []
    os.makedirs("temp_tts", exist_ok=True)
    idx = 0
    for s, e, txt in items:
        if not txt or txt.startswith("["): continue
        p_raw, p_fit = f"temp_tts/r_{idx}.mp3", f"temp_tts/f_{idx}.mp3"
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
    
    tts_audio = "full_tts.wav"
    subprocess.run(["ffmpeg", "-y"] + inputs + ["-filter_complex", "".join(parts) + f"{''.join([f'[a{i}]' for i in range(idx)])}amix=inputs={idx}:dropout_transition=0:normalize=0[out]", "-map", "[out]", tts_audio], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("[*] Render video hoàn chỉnh...")
    w, h = get_video_dimensions(raw_video)
    y_start = (int(h * y1_pct) // 2) * 2
    crop_h = max(24, min((int(h * (y2_pct - y1_pct)) // 2) * 2, h - y_start))
    center_margin_v = max(4, int((1.0 - (y1_pct + y2_pct)/2.0) * 288 - 4))
    
    t_cre = CRE_NAME.replace("'", "").replace(":", " -")
    t_sub = SUB_NAME.replace("'", "").replace(":", " -")
    t_ep = EPISODE.replace("'", "").replace(":", " -")
    font_org_cmd = f":fontfile='{CJK_FONT}'" if os.path.exists(CJK_FONT) else ""
    
    en_str = "+".join([f"between(t,{max(0, s-0.08):.2f},{e+0.18:.2f})" for s, e, t in items if t]) or "1"
    
    vf = (
        f"[0:v]split[m][c];"
        f"[c]crop=iw:{crop_h}:0:{y_start},gblur=sigma=15[b];"
        f"[m][b]overlay=0:{y_start}:enable='{en_str}'[v1];"
        f"[v1]drawtext=text='cre - {t_cre}':x=25:y=20:fontsize=16{font_org_cmd}:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='sub - {t_sub}':x=25:y=42{font_org_cmd}:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='{t_ep}':x=w-tw-30:y=20:fontsize=22:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='{t_sub}':x='mod(t*65,w+tw)-tw':y='mod(t*32,h+th)-th':fontsize=20:fontcolor=white@0.10,"
        f"subtitles=filename='{srt_file}':force_style='FontName=Comfortaa,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.0,Alignment=2,MarginV={center_margin_v}'[vout]"
    )
    af = "[0:a]volume=0.15[bg];[1:a]volume=1.0[voice];[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    
    subprocess.run(["ffmpeg", "-y", "-i", raw_video, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "Video_HoanThanh.mp4"], check=True)
    print("[✓] ĐÃ XUẤT VIDEO XUẤT SẮC: Video_HoanThanh.mp4")

if __name__ == "__main__":
    main()
