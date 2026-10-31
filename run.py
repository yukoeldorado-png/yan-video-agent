import os, re, time, glob, json, asyncio, subprocess, shutil, edge_tts
from google import genai
from google.genai import types

GEMINI_KEY  = os.environ.get("GEMINI_API_KEY", "").strip()
CRE_NAME   = os.environ.get("CREATOR", "").strip()
SUB_NAME   = "YaN AI"

BODY_TEXT = os.environ.get("RELEASE_BODY", "")
cre_match = re.search(r'(?i)cre\s*[:=]\s*(.+)', BODY_TEXT)
sub_match = re.search(r'(?i)sub\s*[:=]\s*(.+)', BODY_TEXT)

CUSTOM_CRE = cre_match.group(1).strip() if cre_match else CRE_NAME
CUSTOM_SUB = sub_match.group(1).strip() if sub_match else SUB_NAME

client = genai.Client(api_key=GEMINI_KEY)

# ÉP LỆNH TỐI CẤM AI KHÔNG ĐƯỢC LẢI NHẢI
YAN_PROMPT = """
Persona: YaN - Chuyên gia Biên dịch ngôn ngữ hoạt hình Trung Quốc.
TỐI HẬU THƯ CỰC KỲ QUAN TRỌNG:
1. TUYỆT ĐỐI KHÔNG DÙNG TỪ NGỮ CHÀO HỎI (Không "Dưới đây là...", Không "Ok"). Chữ đầu tiên xuất ra BẮT BUỘC phải là số "1".
2. Chỉ dịch lời thoại nhân vật. BỎ QUA HOÀN TOÀN chữ trên màn hình, thông báo hệ thống.
3. Cấu trúc chuẩn từng entry bắt đầu từ số 1:
1
00:00:01,000 --> 00:00:03,000
Câu thoại dịch tiếng Việt

4. Mỗi câu thoại BẮT BUỘC chỉ nằm trên 1 DÒNG ĐƠN. Định dạng: 00:mm:ss,mmm --> 00:mm:ss,mmm.
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

def to_seconds(t_str):
    t_str = t_str.strip().replace('.', ',')
    parts = t_str.split(':')
    if len(parts) == 2:
        m = int(parts[0])
        s_parts = parts[1].split(',')
        s = int(s_parts[0])
        ms = int(s_parts[1][:3].ljust(3, '0')) if len(s_parts) > 1 else 0
        return m * 60 + s + ms / 1000.0
    elif len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s_parts = parts[2].split(',')
        s = int(s_parts[0])
        ms = int(s_parts[1][:3].ljust(3, '0')) if len(s_parts) > 1 else 0
        return h * 3600 + m * 60 + s + ms / 1000.0
    return 0.0

def format_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def extract_subtitles_from_text(raw_text):
    raw_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL | re.IGNORECASE)
    raw_text = raw_text.replace('\r\n', '\n').replace('\r', '\n')
    raw_text = re.sub(r'^```(?:srt)?\s*', '', raw_text, flags=re.MULTILINE)
    raw_text = re.sub(r'\s*```$', '', raw_text, flags=re.MULTILINE)
    raw_text = raw_text.replace('：', ':').replace('，', ',').replace('—>', '-->').replace('->', '-->')
    
    ts_pattern = r'((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,\.]\d{1,3})?)\s*(?:-->|->|—|–|~|-)\s*((?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,\.]\d{1,3})?)'
    matches = list(re.finditer(ts_pattern, raw_text))
    
    items = []
    for i in range(len(matches)):
        st_sec = to_seconds(matches[i].group(1))
        et_sec = to_seconds(matches[i].group(2))
        p1 = matches[i].end()
        p2 = matches[i+1].start() if i+1 < len(raw_text) else len(raw_text)
        
        lines = [l.strip() for l in raw_text[p1:p2].split('\n') if l.strip()]
        valid = [l for l in lines if not re.match(r'^\d+$', l) and '-->' not in l]
        txt = " ".join(valid).replace('"', '').strip()
        
        if txt and len(txt.split()) < 50 and et_sec > st_sec:
            items.append((st_sec, et_sec, txt))
            
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
    print(f"\n=================================================")
    print(f"[*] ĐANG XỬ LÝ VIDEO: {base_name} | Cre: {CUSTOM_CRE or 'Ẩn'} | Sub: {CUSTOM_SUB}")
    print(f"=================================================")

    clean_vid = f"clean_{base_name}.mp4"
    shutil.copyfile(video_path, clean_vid)

    print("[*] Đang tải video lên Google Gemini...")
    v_file = client.files.upload(file=clean_vid)
    for _ in range(30):
        if v_file.state.name != "PROCESSING": break
        time.sleep(1.5)
        v_file = client.files.get(name=v_file.name)

    config = types.GenerateContentConfig(system_instruction=YAN_PROMPT, temperature=0.1)
    user_prompt = "Dịch TOÀN BỘ video. Cấm nói chuyện. Bắt đầu ngay bằng số 1:"

    resp = None
    for m in ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.8-flash"]:
        try:
            print(f"[*] Đang bóc sub với model {m}...")
            r = client.models.generate_content(model=m, contents=[v_file, user_prompt], config=config)
            if r and r.text and "-->" in r.text:
                resp = r
                print(f"[✓] Model {m} xuất phụ đề thành công!")
                break
        except Exception as e:
            print(f"[!] {m} bận: {e}")
            time.sleep(2.0)
            
    try: client.files.delete(name=v_file.name)
    except: pass
    
    if not resp or not resp.text:
        raise Exception("Không thể lấy phụ đề từ Google Gemini!")

    items = extract_subtitles_from_text(resp.text)
    print(f"[✓] Đã bóc tách được {len(items)} câu thoại chuẩn!")
    
    if not items:
        print("\n" + "="*50)
        print("🚨 LỖI NGHIÊM TRỌNG: AI TRẢ VỀ ĐỊNH DẠNG SAI!")
        print("👇 CHI TIẾT NỘI DUNG AI NÓI (DEBUG):")
        print(resp.text[:1500])
        print("="*50 + "\n")
        raise Exception("Không nhận diện được câu thoại nào! Hãy xem chi tiết nội dung AI nói ở log bên trên.")

    srt_file = f"sub_{base_name}.srt"
    with open(srt_file, "w", encoding="utf-8") as f:
        for idx, (s, e, t) in enumerate(items, 1):
            f.write(f"{idx}\n{format_time(s)} --> {format_time(e)}\n{t}\n\n")

    print("[*] Đang lồng tiếng CapCut (có timeout ngắt mạng)...")
    inputs, parts = [], []
    temp_dir = f"temp_tts_{base_name}"
    os.makedirs(temp_dir, exist_ok=True)
    idx = 0
    for s, e, txt in items:
        p_raw, p_fit = f"{temp_dir}/r_{idx}.mp3", f"{temp_dir}/f_{idx}.mp3"
        if generate_voice(txt, p_raw):
            dur = get_audio_duration(p_raw)
            max_d = max(0.5, e - s - 0.08)
            chosen_p = p_raw
            if dur > max_d:
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", p_raw, "-filter:a", f"atempo={min(1.85, dur/max_d):.2f}", "-vn", p_fit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                chosen_p = p_fit
            inputs.extend(["-i", chosen_p])
            d_ms = int(s * 1000)
            parts.append(f"[{idx}]adelay={d_ms}|{d_ms}[a{idx}];")
            idx += 1

    tts_audio = f"tts_{base_name}.wav"
    has_audio = False
    if idx > 0:
        cmd_tts = ["ffmpeg", "-nostdin", "-y"] + inputs + ["-filter_complex", "".join(parts) + f"{''.join([f'[a{i}]' for i in range(idx)])}amix=inputs={idx}:dropout_transition=0:normalize=0[out]", "-map", "[out]", tts_audio]
        subprocess.run(cmd_tts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if os.path.exists(tts_audio) and os.path.getsize(tts_audio) > 1000:
            has_audio = True

    w, h = get_video_dimensions(clean_vid)
    y1_pct, y2_pct = 0.90, 1.00
    y_start = (int(h * y1_pct) // 2) * 2
    crop_h = max(24, min((int(h * (y2_pct - y1_pct)) // 2) * 2, h - y_start))
    center_margin_v = max(4, int(h - (y_start + crop_h//2) - 7))

    cre_cmd = f"drawtext=text='cre - {CUSTOM_CRE}':x=25:y=20:fontsize=16:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1," if (CUSTOM_CRE and CUSTOM_CRE != "????") else ""
    t_sub = CUSTOM_SUB.replace("'", "").replace(":", " -")
    
    en_str = "+".join([f"between(t,{max(0, s-0.08):.2f},{e+0.18:.2f})" for s, e, t in items]) or "1"

    vf = (
        f"[0:v]split[m][c];"
        f"[c]crop=iw:{crop_h}:0:{y_start},gblur=sigma=15[b];"
        f"[m][b]overlay=0:{y_start}:enable='{en_str}'[v1];"
        f"[v1]{cre_cmd}"
        f"drawtext=text='sub - {t_sub}':x=25:y=42:fontsize=16:fontcolor=white:shadowcolor=black:shadowx=1:shadowy=1,"
        f"drawtext=text='{t_sub}':x='mod(t*65,w+tw)-tw':y='mod(t*32,h+th)-th':fontsize=20:fontcolor=white@0.10,"
        f"subtitles=filename='{srt_file}':force_style='FontName=Comfortaa,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.0,Alignment=2,MarginV={center_margin_v}'[vout]"
    )

    output_filename = f"Video_HoanThanh_{base_name}.mp4"
    if has_audio:
        duck_str = "+".join([f"between(t,{max(0, s-0.05):.2f},{e+0.05:.2f})" for s, e, t in items]) or "0"
        af = f"[0:a]stereotools=mlev=0.1,volume='if({duck_str}, 0.05, 1.0)':eval=frame[bg];[1:a]volume=1.0[voice];[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", output_filename]
    else:
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-filter_complex", vf, "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "copy", output_filename]

    print("[*] Đang render video hoàn chỉnh bằng FFmpeg...")
    res = subprocess.run(cmd_render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
    if res.returncode != 0:
        raise Exception(f"Lỗi FFmpeg: {res.stderr[-300:]}")

    print(f"[✓] Đã render xong: {output_filename}")
    if tag_name:
        subprocess.run(["gh", "release", "upload", tag_name, output_filename, "--clobber"], timeout=60)
        print(f"[✓] Đã đính kèm video vào mục Release!")
        
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    files = glob.glob("input_dir/*.*")
    video_files = sorted([f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv')) and "video_hoanthanh" not in f.lower()])
    if not video_files: raise Exception("Không tìm thấy file video nào tải lên!")

    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        tag = subprocess.run(["gh", "release", "view", "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True, timeout=10).stdout.strip()
        
    print(f"[*] TÌM THẤY {len(video_files)} VIDEO. HỆ THỐNG BẮT ĐẦU CHẠY HÀNG LOẠT...")
    for vid in video_files:
        try:
            process_single_video(vid, tag)
        except Exception as e:
            print(f"[!] BỎ QUA VIDEO {vid} DO LỖI: {e}")

if __name__ == "__main__":
    main()n=first:dropout_transition=0[aout]"
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", output_filename]
    else:
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-filter_complex", vf, "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "copy", output_filename]

    print("[*] Đang render video hoàn chỉnh bằng FFmpeg...")
    res = subprocess.run(cmd_render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
    if res.returncode != 0:
        raise Exception(f"Lỗi FFmpeg: {res.stderr[-300:]}")

    print(f"[✓] Đã render xong: {output_filename}")
    if tag_name:
        subprocess.run(["gh", "release", "upload", tag_name, output_filename, "--clobber"], timeout=60)
        print(f"[✓] Đã đính kèm video vào mục Release!")
        
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    files = glob.glob("input_dir/*.*")
    video_files = sorted([f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv')) and "video_hoanthanh" not in f.lower()])
    if not video_files: raise Exception("Không tìm thấy file video nào tải lên!")

    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        tag = subprocess.run(["gh", "release", "view", "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True, timeout=10).stdout.strip()
        
    print(f"[*] TÌM THẤY {len(video_files)} VIDEO. HỆ THỐNG BẮT ĐẦU CHẠY HÀNG LOẠT...")
    for vid in video_files:
        try:
            process_single_video(vid, tag)
        except Exception as e:
            print(f"[!] BỎ QUA VIDEO {vid} DO LỖI: {e}")

if __name__ == "__main__":
    main()0.1,volume='if({duck_str}, 0.05, 1.0)':eval=frame[bg];[1:a]volume=1.0[voice];[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", output_filename]
    else:
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-filter_complex", vf, "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "copy", output_filename]

    print("[*] Đang render video hoàn chỉnh bằng FFmpeg...")
    res = subprocess.run(cmd_render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
    if res.returncode != 0:
        raise Exception(f"Lỗi FFmpeg: {res.stderr[-300:]}")

    print(f"[✓] Đã render xong: {output_filename}")
    if tag_name:
        subprocess.run(["gh", "release", "upload", tag_name, output_filename, "--clobber"], timeout=60)
        print(f"[✓] Đã đính kèm video vào mục Release!")
        
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    files = glob.glob("input_dir/*.*")
    video_files = sorted([f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv')) and "video_hoanthanh" not in f.lower()])
    if not video_files: raise Exception("Không tìm thấy file video nào tải lên!")

    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        tag = subprocess.run(["gh", "release", "view", "--json", "tagName", "-q", ".tagName"], capture_output=True, text=True, timeout=10).stdout.strip()
        
    print(f"[*] TÌM THẤY {len(video_files)} VIDEO. HỆ THỐNG BẮT ĐẦU CHẠY HÀNG LOẠT...")
    for vid in video_files:
        try:
            process_single_video(vid, tag)
        except Exception as e:
            print(f"[!] BỎ QUA VIDEO {vid} DO LỖI: {e}")

if __name__ == "__main__":
    main()':fontsize=20:fontcolor=white@0.10,"
        f"subtitles=filename='{srt_file}':force_style='FontName=Comfortaa,FontSize=15,Bold=1,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.0,Alignment=2,MarginV={center_margin_v}'[vout]"
    )

    output_filename = f"Video_HoanThanh_{base_name}.mp4"
    if has_audio:
        # Dynamic ducking: Giảm 5% lúc nói, 100% tiếng cười/BGM
        duck_str = "+".join([f"between(t,{max(0, s-0.05):.2f},{e+0.05:.2f})" for s, e, t in items]) or "0"
        output_filename = f"Video_HoanThanh_{base_name}.mp4"
    if has_audio:
        duck_str = "+".join([f"between(t,{max(0, s-0.05):.2f},{e+0.05:.2f})" for s, e, t in items]) or "0"
        af = f"[0:a]stereotools=mlev=0.1,volume='if({duck_str}, 0.05, 1.0)':eval=frame[bg];[1:a]volume=1.0[voice];[bg][voice]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-i", tts_audio, "-filter_complex", f"{vf};{af}", "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", output_filename]
    else:
        cmd_render = ["ffmpeg", "-nostdin", "-y", "-i", clean_vid, "-filter_complex", vf, "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-c:a", "copy", output_filename]

    print("[*] Đang render video hoàn chỉnh bằng FFmpeg...")
    res = subprocess.run(cmd_render, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
    if res.returncode != 0:
        raise Exception(f"Lỗi FFmpeg: {res.stderr[-300:]}")

    print(f"[✓] Đã render xong: {output_filename}")
    if tag_name:
        subprocess.run(["gh", "release", "upload", tag_name, output_filename, "--clobber"], timeout=60)
        print(f"[✓] Đã đính kèm video vào mục Release!")
        
    shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    files = glob.glob("input_dir/*.*")
    video_files = sorted([f for f in files if f.lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.flv')) and "video_hoanthanh" not in f.lower()])
    if not video_files: raise Exception("Không tìm thấy file video nào tải lên!")

    tag = os.environ.get("RELEASE_TAG", "").strip()
    if not tag:
        tag = subprocess.run(["gh", "release", "view", "--json", "tagName", -q, ".tagName"], capture_output=True, text=True, timeout=10).stdout.strip()
        
    print(f"[*] TÌM THẤY {len(video_files)} VIDEO. HỆ THỐNG BẮT ĐẦU CHẠY HÀNG LOẠT...")
    for vid in video_files:
        try:
            process_single_video(vid, tag)
        except Exception as e:
            print(f"[!] BỎ QUA VIDEO {vid} DO LỖI: {e}")

if __name__ == "__main__":
    main()
