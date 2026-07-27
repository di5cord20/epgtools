#!/usr/bin/env python3
import json
import os
import re
import subprocess
from pathlib import Path
from typing import List

import gradio as gr
import requests

EPG_GURU_PRESETS = {
    "Canada": {
        "channel_list": "https://epg.guru/IPTV_Channel_List/Canada_channel_list.txt",
        "xml_source": "https://cdn.epg.guru/7dayiptv/Canada.xml",
    },
    "United States": {
        "channel_list": "https://epg.guru/IPTV_Channel_List/UnitedStates_channel_list.txt",
        "xml_source": "https://cdn.epg.guru/7dayiptv/UnitedStates.xml",
    },
    "USFast": {
        "channel_list": "https://epg.guru/IPTV_Channel_List/USFast_channel_list.txt",
        "xml_source": "https://cdn.epg.guru/7dayiptv/USFast.xml",
    },
    "United Kingdom": {
        "channel_list": "https://epg.guru/IPTV_Channel_List/UnitedKingdom_channel_list.txt",
        "xml_source": "https://cdn.epg.guru/7dayiptv/UnitedKingdom.xml",
    },
    "Australia": {
        "channel_list": "https://epg.guru/IPTV_Channel_List/Australia_channel_list.txt",
        "xml_source": "https://cdn.epg.guru/7dayiptv/Australia.xml",
    },
}

CONFIG_DIR = Path("./config")
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

GLOBAL_LISTBOX_JS = """
async () => {
    // Selection logic for div-based listboxes
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('listbox-item')) {
            e.target.classList.toggle('selected');
        }
    });

    globalThis.filterAvailableList = function() {
        var input = document.getElementById('listbox_search');
        if (!input) return;
        var filter = input.value.toLowerCase();
        var items = document.querySelectorAll('#left_listbox .listbox-item');
        items.forEach(item => {
            var txt = item.innerText.toLowerCase();
            item.style.display = txt.includes(filter) ? "" : "none";
        });
    };

    globalThis.moveSelected = function(fromId, toId) {
        var fromBox = document.getElementById(fromId);
        var toBox = document.getElementById(toId);
        var selected = fromBox.querySelectorAll('.selected');
        selected.forEach(item => {
            item.classList.remove('selected');
            toBox.appendChild(item);
        });
        globalThis.sortListBox(toBox);
        globalThis.updateLabelsAndSync();
    };

    globalThis.moveAll = function(fromId, toId) {
        var fromBox = document.getElementById(fromId);
        var toBox = document.getElementById(toId);
        var items = Array.from(fromBox.querySelectorAll('.listbox-item'))
                         .filter(i => i.style.display !== "none");
        items.forEach(item => {
            item.classList.remove('selected');
            toBox.appendChild(item);
        });
        globalThis.sortListBox(toBox);
        globalThis.updateLabelsAndSync();
    };

    globalThis.sortListBox = function(box) {
        var items = Array.from(box.querySelectorAll('.listbox-item'));
        items.sort((a, b) => a.innerText.localeCompare(b.innerText));
        box.innerHTML = "";
        items.forEach(item => box.appendChild(item));
    };

    globalThis.updateLabelsAndSync = function() {
        var leftBox = document.getElementById('left_listbox');
        var rightBox = document.getElementById('right_listbox');
        document.getElementById('left_listbox_label').innerText = "Available Channels (" + leftBox.children.length + ")";
        document.getElementById('right_listbox_label').innerText = "Selected Channels (" + rightBox.children.length + ")";

        var selectedChannels = Array.from(rightBox.children).map(i => i.innerText);
        var jsonStr = JSON.stringify(selectedChannels);
        
        var hiddenInput = document.querySelector("#hidden_json_input textarea");
        if (hiddenInput) {
            hiddenInput.value = jsonStr;
            hiddenInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
    };
}
"""


def fetch_channel_list(list_url_or_path: str) -> List[str]:
    if not list_url_or_path:
        return []
    if list_url_or_path.startswith("http://") or list_url_or_path.startswith("https://"):
        try:
            resp = requests.get(list_url_or_path, timeout=15)
            resp.raise_for_status()
            content = resp.text
        except Exception as e:
            raise gr.Error(f"Failed to fetch remote channel list: {e}")
    else:
        path = Path(list_url_or_path)
        if not path.exists():
            raise gr.Error(f"Local channel list file not found: {list_url_or_path}")
        content = path.read_text(encoding="utf-8")

    parsed_channels = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed_channels.append(line)

    return parsed_channels


def render_dual_listbox(avail_channels: List[str], selected_channels: List[str]) -> str:
    # Helper to build the item divs
    def build_items(channels):
        return "".join([f'<div class="listbox-item" style="padding: 5px; cursor: pointer; border-bottom: 1px solid #2d3748;">{ch}</div>' for ch in channels])

    return f"""
    <style>
        .listbox-item.selected {{ background-color: #3182ce !important; color: white; }}
        .listbox-item:hover {{ background-color: #2d3748; }}
        .listbox-container {{ height: 420px; overflow-y: auto; background: #1a202c; border: 1px solid #4a5568; border-radius: 6px; }}
    </style>
    <div style="font-family: system-ui, sans-serif; display: flex; flex-direction: column; gap: 10px;">
        <input type="text" id="listbox_search" placeholder="Filter..." onkeyup="globalThis.filterAvailableList()" style="padding: 10px; border-radius: 6px; background: #1a202c; color: white; border: 1px solid #4a5568;" />
        
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1;">
                <label id="left_listbox_label" style="font-weight: bold;">Available ({len(avail_channels)})</label>
                <div id="left_listbox" class="listbox-container">{build_items(avail_channels)}</div>
            </div>
            
            <div style="display: flex; flex-direction: column; justify-content: center; gap: 10px;">
                <button onclick="globalThis.moveAll('left_listbox', 'right_listbox')"> &gt;&gt; </button>
                <button onclick="globalThis.moveSelected('left_listbox', 'right_listbox')"> &gt; </button>
                <button onclick="globalThis.moveSelected('right_listbox', 'left_listbox')"> &lt; </button>
                <button onclick="globalThis.moveAll('right_listbox', 'left_listbox')"> &lt;&lt; </button>
            </div>

            <div style="flex: 1;">
                <label id="right_listbox_label" style="font-weight: bold;">Selected ({len(selected_channels)})</label>
                <div id="right_listbox" class="listbox-container">{build_items(selected_channels)}</div>
            </div>
        </div>
    </div>
    """


def handle_source_change(preset_selection: str):
    if preset_selection in EPG_GURU_PRESETS:
        list_url = EPG_GURU_PRESETS[preset_selection]["channel_list"]
        xml_url = EPG_GURU_PRESETS[preset_selection]["xml_source"]
    else:
        return "", "", gr.update(value=render_dual_listbox([], [])), "[]", "Invalid source preset selected."

    try:
        channels = fetch_channel_list(list_url)
        status = f"Loaded {len(channels)} total available channels from source."
        return (
            list_url,
            xml_url,
            gr.update(value=render_dual_listbox(channels, [])),
            "[]",
            status,
        )
    except Exception as e:
        return list_url, xml_url, gr.update(value=render_dual_listbox([], [])), "[]", f"Error: {e}"


def list_existing_configs():
    files = [f.name for f in CONFIG_DIR.glob("*.txt")]
    return gr.update(choices=sorted(files))


def load_existing_config(config_filename: str, active_list_url: str):
    if not config_filename:
        return (
            gr.update(value=render_dual_listbox([], [])),
            "[]",
            "",
            "",
            "No file selected to load.",
            gr.update(interactive=False),
        )

    file_path = CONFIG_DIR / config_filename
    if not file_path.exists():
        return (
            gr.update(value=render_dual_listbox([], [])),
            "[]",
            "",
            "",
            "File does not exist.",
            gr.update(interactive=False),
        )

    sources = []
    loaded_channels = []

    content = file_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("# SOURCES:"):
            url = line.split(":", 1)[1].strip()
            sources.append(url)
        else:
            loaded_channels.append(line)

    xml_source = sources[0] if sources else ""

    avail_channels = []
    if active_list_url:
        try:
            avail_channels = fetch_channel_list(active_list_url)
        except Exception:
            pass

    remaining_avail = [ch for ch in avail_channels if ch not in loaded_channels]

    return (
        gr.update(value=render_dual_listbox(remaining_avail, sorted(loaded_channels))),
        json.dumps(sorted(loaded_channels)),
        config_filename.replace(".txt", ""),
        xml_source,
        f"Loaded {len(loaded_channels)} channels from {config_filename}.",
        gr.update(interactive=True),
    )


def save_config_file(config_name: str, xml_source_url: str, selected_json: str):
    if not config_name.strip():
        return "Error: Please enter a valid config filename.", gr.update(interactive=False)
    if not xml_source_url.strip():
        return "Error: XML Source URL is missing.", gr.update(interactive=False)

    try:
        selected_channels = json.loads(selected_json) if selected_json else []
    except Exception:
        selected_channels = []

    if not selected_channels:
        return "Error: No channels selected to save.", gr.update(interactive=False)

    clean_name = re.sub(r"[^\w\-_]", "", config_name.strip())
    target_path = CONFIG_DIR / f"{clean_name}.txt"

    lines = [f"# SOURCES: {xml_source_url.strip()}\n"]
    lines.extend(sorted(selected_channels))

    target_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (
        f"Successfully saved configuration to {target_path} ({len(selected_channels)} channels)!",
        gr.update(interactive=True),
    )


def run_epg_merge(config_name: str, force_download: bool, delete_uncompressed: bool, progress=gr.Progress()):
    clean_name = re.sub(r"[^\w\-_]", "", config_name.strip())
    config_file = CONFIG_DIR / f"{clean_name}.txt"

    if not config_file.exists():
        return f"Error: Config file {config_file} does not exist. Please save it first."

    cmd = ["python3", "-u", "epg_merge.py", "-i", str(config_file)]
    if force_download:
        cmd.append("-f")
    if delete_uncompressed:
        cmd.append("-d")

    summary_log = []
    
    progress(0.1, desc="[1/5] Starting EPG processing...")

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )

        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue

            line_lower = line.lower()
            if "downloading" in line_lower or "fetching" in line_lower:
                progress(0.25, desc="[2/5] Downloading XML EPG source file...")
                summary_log.append("▶ Downloading source EPG file...")
            elif "loading channels" in line_lower or "parsing channels" in line_lower or "channel" in line_lower and "%" in line_lower:
                match = re.search(r"(\d+)%", line)
                pct = int(match.group(1)) if match else 50
                calc_progress = 0.3 + (pct / 100.0) * 0.2
                progress(calc_progress, desc=f"[3/5] Loading channels ({pct}% done)...")
            elif "channels loaded" in line_lower or "parsed channels" in line_lower:
                progress(0.55, desc="[3/5] Channels loaded successfully!")
                summary_log.append(f"✔ {line}")
            elif "loading programs" in line_lower or "parsing programs" in line_lower or "program" in line_lower and "%" in line_lower:
                match = re.search(r"(\d+)%", line)
                pct = int(match.group(1)) if match else 50
                calc_progress = 0.6 + (pct / 100.0) * 0.35
                progress(calc_progress, desc=f"[4/5] Loading program guide data ({pct}% done)...")
            elif "programs loaded" in line_lower or "parsed programs" in line_lower:
                progress(0.95, desc="[4/5] Programs loaded successfully!")
                summary_log.append(f"✔ {line}")
            elif "done" in line_lower or "saved" in line_lower or "written" in line_lower or "success" in line_lower:
                summary_log.append(f"✔ {line}")

        process.wait()

        if process.returncode == 0:
            progress(1.0, desc="[5/5] Processing completed successfully!")
            final_summary = "\n".join(summary_log) if summary_log else "EPG Merge finished successfully."
            return f"STATUS: SUCCESS\n\n{final_summary}"
        else:
            return f"STATUS: FAILED (Exit Code {process.returncode})\n\nOutput Log:\n" + "\n".join(summary_log)

    except Exception as e:
        return f"Error executing merger: {e}"


with gr.Blocks(title="EPG Config Builder") as app:
    gr.Markdown("# EPG Channel Configurator & Merger")

    active_list_url = gr.State("")
    active_xml_url = gr.State("")

    with gr.Row():
        with gr.Column(scale=2):
            preset_dropdown = gr.Dropdown(
                choices=list(EPG_GURU_PRESETS.keys()),
                value="Canada",
                label="1. Select EPG Source Region",
            )

        with gr.Column(scale=1):
            existing_config_dropdown = gr.Dropdown(
                choices=[],
                label="Load Saved Config File",
            )
            load_btn = gr.Button("Load Config", variant="secondary")

    status_box = gr.Textbox(label="Status / Log", interactive=False)

    gr.Markdown("---")
    gr.Markdown("### 2. Channel Selection Box")

    dual_listbox_html = gr.HTML(render_dual_listbox([], []))

    with gr.Group(elem_id="hidden_json_container"):
        gr.HTML("<style>#hidden_json_container { display: none !important; }</style>")
        hidden_json_input = gr.Textbox(elem_id="hidden_json_input", value="[]")

    gr.Markdown("---")
    gr.Markdown("### 3. Save & Output")

    with gr.Row():
        save_filename_input = gr.Textbox(
            label="Config Output Filename",
            placeholder="e.g. canada or uk",
            info="Will save to ./config/<filename>.txt",
        )
        save_btn = gr.Button("Save Configuration", variant="primary")

    with gr.Accordion("Run Merger Directly", open=False):
        gr.Markdown("*Note: Save configuration or load a saved config file first to enable the run button below.*")
        with gr.Row():
            force_chk = gr.Checkbox(label="Force Re-download (-f)", value=False)
            delete_xml_chk = gr.Checkbox(label="Delete Uncompressed XML (-d)", value=False)
            run_merge_btn = gr.Button(
                "Run epg_merge.py Now",
                variant="stop",
                interactive=False,
            )

        merge_output_log = gr.Code(label="Execution Output Log", language="shell")

    preset_dropdown.change(
        fn=handle_source_change,
        inputs=[preset_dropdown],
        outputs=[
            active_list_url,
            active_xml_url,
            dual_listbox_html,
            hidden_json_input,
            status_box,
        ],
    )

    load_btn.click(
        fn=load_existing_config,
        inputs=[existing_config_dropdown, active_list_url],
        outputs=[
            dual_listbox_html,
            hidden_json_input,
            save_filename_input,
            active_xml_url,
            status_box,
            run_merge_btn,
        ],
    )

    save_btn.click(
        fn=save_config_file,
        inputs=[save_filename_input, active_xml_url, hidden_json_input],
        outputs=[status_box, run_merge_btn],
    )

    run_merge_btn.click(
        fn=run_epg_merge,
        inputs=[save_filename_input, force_chk, delete_xml_chk],
        outputs=[merge_output_log],
    )

    # Attach JS first
    app.load(fn=None, inputs=None, outputs=None, js=GLOBAL_LISTBOX_JS)

    # Initial loading of presets and files
    app.load(
        fn=handle_source_change,
        inputs=[preset_dropdown],
        outputs=[
            active_list_url,
            active_xml_url,
            dual_listbox_html,
            hidden_json_input,
            status_box,
        ],
    )
    app.load(fn=list_existing_configs, outputs=[existing_config_dropdown])

if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860)
