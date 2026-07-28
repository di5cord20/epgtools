#!/usr/bin/env python3
"""
EPG Channel Configurator & Merger -- Flask edition.

Drop-in replacement for the Gradio version. Same filename, same port
(7860), same ./config directory and epg_merge.py contract -- only the UI
framework changed, to cut ~300MB of transitive dependencies (pandas,
numpy, pillow, huggingface_hub, pydantic, ...) that Gradio pulls in but
this tool never actually uses.

Run: python3 -u epg_gui.py
"""

import json
import re
import subprocess
from pathlib import Path
from typing import List

import requests
from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

EPG_GURU_PRESETS = {
    "Canada": {
        "slug": "Canada",
        "channel_list": "https://epg.guru/IPTV_Channel_List/Canada_channel_list.txt",
    },
    "United States": {
        "slug": "UnitedStates",
        "channel_list": "https://epg.guru/IPTV_Channel_List/UnitedStates_channel_list.txt",
    },
    "USFast": {
        "slug": "USFast",
        "channel_list": "https://epg.guru/IPTV_Channel_List/USFast_channel_list.txt",
    },
    "United Kingdom": {
        "slug": "UnitedKingdom",
        "channel_list": "https://epg.guru/IPTV_Channel_List/UnitedKingdom_channel_list.txt",
    },
    "Australia": {
        "slug": "Australia",
        "channel_list": "https://epg.guru/IPTV_Channel_List/Australia_channel_list.txt",
    },
}

CONFIG_DIR = Path("./config")
CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def fetch_channel_list(list_url_or_path: str) -> List[str]:
    """Same behavior as the original: accepts a remote URL or a local path."""
    if not list_url_or_path:
        return []
    if list_url_or_path.startswith("http://") or list_url_or_path.startswith("https://"):
        resp = requests.get(list_url_or_path, timeout=15)
        resp.raise_for_status()
        content = resp.text
    else:
        path = Path(list_url_or_path)
        if not path.exists():
            raise FileNotFoundError(f"Local channel list file not found: {list_url_or_path}")
        content = path.read_text(encoding="utf-8")

    parsed = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed.append(line)
    return parsed


def sanitize_name(name: str) -> str:
    return re.sub(r"[^\w\-_]", "", (name or "").strip())


@app.route("/")
def index():
    resp = render_template("index.html", presets=EPG_GURU_PRESETS)
    return Response(resp, headers={"Cache-Control": "no-store"})


@app.route("/api/presets")
def api_presets():
    return jsonify(EPG_GURU_PRESETS)


@app.route("/api/channels")
def api_channels():
    list_url = request.args.get("list", "").strip()
    if not list_url:
        return jsonify({"error": "list is required"}), 400
    try:
        channels = fetch_channel_list(list_url)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"channels": channels})


@app.route("/api/configs")
def api_configs():
    return jsonify(sorted(f.name for f in CONFIG_DIR.glob("*.txt")))


@app.route("/api/configs/<name>", methods=["GET"])
def api_load_config(name):
    file_path = CONFIG_DIR / name
    if not file_path.exists() or file_path.suffix != ".txt":
        return jsonify({"error": "Config file not found"}), 404

    sources = []
    channels = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("# SOURCES:"):
            sources.append(line.split(":", 1)[1].strip())
        else:
            channels.append(line)

    return jsonify({
        "xml_source": sources[0] if sources else "",
        "channels": sorted(channels),
    })


@app.route("/api/configs/<name>", methods=["DELETE"])
def api_delete_config(name):
    # Path(name).name strips any directory components -- confines the
    # delete to a plain filename inside CONFIG_DIR, same protection the
    # other endpoints get from sanitize_name.
    file_path = CONFIG_DIR / Path(name).name
    if file_path.suffix != ".txt" or not file_path.exists():
        return jsonify({"error": "Config file not found"}), 404

    file_path.unlink()
    return jsonify({"deleted": file_path.name})


@app.route("/api/configs/<name>", methods=["POST"])
def api_save_config(name):
    clean_name = sanitize_name(Path(name).stem)
    if not clean_name:
        return jsonify({"error": "Invalid config filename"}), 400

    data = request.get_json(force=True) or {}
    xml_source = (data.get("xml_source") or "").strip()
    channels = data.get("channels") or []

    if not xml_source:
        return jsonify({"error": "xml_source is required"}), 400
    if not channels:
        return jsonify({"error": "No channels selected"}), 400

    target_path = CONFIG_DIR / f"{clean_name}.txt"
    lines = [f"# SOURCES: {xml_source}\n"]
    lines.extend(sorted(channels))
    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return jsonify({"saved": str(target_path), "count": len(channels), "filename": f"{clean_name}.txt"})


@app.route("/api/run-merge", methods=["POST"])
def api_run_merge():
    """
    Streams epg_merge.py's stdout back as it runs, using Server-Sent
    Events. The frontend renders each line into a live log panel.

    Note: this streams the raw log rather than reconstructing the
    Gradio version's percentage-estimate progress bar -- simpler, and
    still gives a live view of what's happening.
    """
    data = request.get_json(force=True) or {}
    clean_name = sanitize_name(Path(data.get("config_name", "")).stem)
    force = bool(data.get("force"))
    delete_uncompressed = bool(data.get("delete_uncompressed"))

    config_file = CONFIG_DIR / f"{clean_name}.txt"
    if not config_file.exists():
        return jsonify({"error": f"Config file {config_file} does not exist. Save it first."}), 400

    cmd = ["python3", "-u", "epg_merge.py", "-i", str(config_file)]
    if force:
        cmd.append("-f")
    if delete_uncompressed:
        cmd.append("-d")

    def generate():
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line:
                yield f"data: {json.dumps({'line': line})}\n\n"
        process.wait()
        yield f"data: {json.dumps({'done': True, 'returncode': process.returncode})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    from waitress import serve
    # threads=8: enough that one slow /api/channels or /api/run-merge call
    # (which can take minutes for a large region) no longer blocks every
    # other request -- the actual cause of the app appearing to "hang".
    serve(app, host="0.0.0.0", port=7860, threads=8)
