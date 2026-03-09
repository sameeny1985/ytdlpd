import threading
import queue
import os
import re

from flask import Flask, render_template, request, jsonify, send_from_directory
from yt_dlp import YoutubeDL

app = Flask(__name__)

job_queue = queue.Queue()

progress = {
    "state": "idle",
    "current_index": 0,
    "total": 0,
    "current_title": "",
    "percent": "",
    "speed": "",
    "eta": "",
    "filename": "",
    "log": []
}

OUTPUT_DIR = os.path.abspath("downloads")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================
# Utils
# =========================
def append_log(msg):
    progress["log"].append(msg)
    if len(progress["log"]) > 500:
        progress["log"] = progress["log"][-500:]


# =========================
# Youtube Regex
# =========================
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+",
    re.IGNORECASE
)

def is_youtube_url(text):
    return bool(YOUTUBE_REGEX.search(text or ""))


# =========================
# yt-dlp hooks
# =========================
def progress_hook(d):
    if d.get("status") == "downloading":
        progress["state"] = "downloading"
        progress["percent"] = (d.get("_percent_str") or "").strip()
        progress["eta"] = d.get("_eta_str") or ""
        progress["speed"] = d.get("_speed_str") or ""
        progress["filename"] = os.path.basename(d.get("filename") or "")
    elif d.get("status") == "finished":
        progress["percent"] = "100%"
        progress["eta"] = "0"
        progress["speed"] = ""
        append_log("✅ Finished: " + progress["filename"])


def build_ydl_opts(quality, ffmpeg_location):
    if quality == "240":
        fmt = "133+140"
    elif quality == "360":
        fmt = "18"
    else:
        fmt = "bestvideo+bestaudio"

  ydl_opts = {
    "format": fmt,
    "merge_output_format": "mp4",
    "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s.%(ext)s"),
    "progress_hooks": [progress_hook],
    "restrictfilenames": True,
    "quiet": True,
    "noprogress": True,
    "nocheckcertificate": True,
    "cookiefile": "cookies.txt"
}

    if ffmpeg_location:
        ydl_opts["ffmpeg_location"] = ffmpeg_location

    return ydl_opts


# =========================
# Analyze links
# =========================
def analyze_links(raw_links):
    links = [ln.strip() for ln in raw_links.splitlines() if ln.strip()]
    results = []

    ydl_opts = {"quiet": True, "nocheckcertificate": True}

    with YoutubeDL(ydl_opts) as ydl:
        for ln in links:
            try:
                info = ydl.extract_info(ln, download=False)
                results.append({
                    "title": info.get("title", "بدون عنوان"),
                    "thumbnail": info.get("thumbnail", ""),
                    "url": info.get("webpage_url", ln),
                    "id": info.get("id", ""),
                    "error": None
                })
            except Exception as e:
                err_msg = str(e)
                print(f"[ERROR] {err_msg}")  # لاگ سرور
                results.append({
                    "title": f"خطا در خواندن: {ln}",
                    "thumbnail": "",
                    "url": ln,
                    "id": "",
                    "error": err_msg  # پیام واقعی خطا برای دیباگ
                })
    return results


# =========================
# Download worker
# =========================
def download_worker():
    while True:
        task = job_queue.get()
        if task is None:
            break

        try:
            links = task["links"]
            quality = task.get("quality", "360")

            progress.update({
                "state": "downloading",
                "current_index": 0,
                "total": len(links),
                "current_title": "",
                "percent": "",
                "speed": "",
                "eta": "",
                "filename": ""
            })

            append_log(f"▶ شروع دانلود {len(links)} لینک")

            ydl_opts = build_ydl_opts(quality, None)

            with YoutubeDL(ydl_opts) as ydl:
                for i, ln in enumerate(links, 1):
                    progress["current_index"] = i
                    try:
                        info = ydl.extract_info(ln, download=False)
                        progress["current_title"] = info.get("title", ln)
                    except Exception as e:
                        progress["current_title"] = ln
                        append_log(f"[ERROR] {str(e)}")
                    append_log(f"⬇ {i}/{len(links)}")
                    ydl.download([ln])

            progress["state"] = "done"

        except Exception as e:
            progress["state"] = "error"
            append_log(str(e))

        finally:
            job_queue.task_done()


threading.Thread(target=download_worker, daemon=True).start()


# =========================
# Routes
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True)
    progress["state"] = "analyzing"
    items = analyze_links(data.get("links", ""))
    progress["state"] = "idle"
    return jsonify({"items": items})


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True)
    links = [ln.strip() for ln in data.get("links", "").splitlines() if ln.strip()]

    progress.update({
        "state": "queued",
        "current_index": 0,
        "total": len(links),
        "log": []
    })

    job_queue.put({
        "links": links,
        "quality": data.get("quality", "360")
    })

    return jsonify({"ok": True})


@app.route("/progress")
def get_progress():
    return jsonify(progress)


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)