#!/usr/bin/env python3
import argparse
import datetime
import gzip
import logging
import os
import re
import shutil
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import List, Set, Tuple, Optional

import requests
from lxml import etree

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("epg_filter")


def format_bytes(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


def parse_config_file(file_path: Path) -> List[str]:
    if not file_path.exists():
        logger.warning(f"Config file not found: {file_path}")
        return []

    cleaned_lines = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                cleaned_lines.append(line)
    return cleaned_lines


def parse_channel_file(file_path: Path) -> Tuple[Set[str], List[str]]:
    if not file_path.exists():
        logger.warning(f"File not found: {file_path}")
        return set(), []

    channel_ids: Set[str] = set()
    sources: List[str] = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            raw_line = line.strip()
            if not raw_line:
                continue

            if raw_line.upper().startswith("# SOURCES:"):
                url = raw_line.split(":", 1)[1].strip()
                if url and url not in sources:
                    sources.append(url)
                continue

            clean_id = raw_line.split("#", 1)[0].strip()
            if clean_id:
                channel_ids.add(clean_id)

    return channel_ids, sources


def load_aggregated_wanted_channels_and_sources(
    input_list_file: Path,
) -> Tuple[Set[str], List[str]]:
    if not input_list_file.exists():
        logger.error(f"Input list file not found: {input_list_file}")
        return set(), []

    config_dir = input_list_file.parent
    aggregated_ids: Set[str] = set()
    aggregated_sources: List[str] = []

    ids, sources = parse_channel_file(input_list_file)
    aggregated_sources.extend(sources)

    referenced_files = []
    with open(input_list_file, "r", encoding="utf-8") as f:
        for line in f:
            clean = line.split("#", 1)[0].strip()
            if clean:
                target_path = config_dir / clean if not Path(clean).is_absolute() else Path(clean)
                if target_path.exists() and target_path.is_file():
                    referenced_files.append(target_path)

    if referenced_files:
        for sub_file in referenced_files:
            sub_ids, sub_sources = parse_channel_file(sub_file)
            aggregated_ids.update(sub_ids)
            for src in sub_sources:
                if src not in aggregated_sources:
                    aggregated_sources.append(src)
    else:
        aggregated_ids.update(ids)

    return aggregated_ids, aggregated_sources


def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", filename)


def normalize_channel_name(raw: str) -> str:
    """
    Reduces a channel name/ID down to just its letters and digits, lowercased,
    with any parenthetical codes stripped -- e.g. "AAJTAK(AAJTK).ca" and
    "Aaj Tak HD" both normalize to "aajtak". Used to match a channel across
    providers whose ID schemes don't line up (e.g. epg.guru's IPTV IDs vs.
    Gracenote's own station IDs) via their human-readable name instead.
    """
    without_parens = re.sub(r"\([^()]*\)", "", raw)
    return re.sub(r"[^a-z0-9]", "", without_parens.lower())


class CacheManager:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_base_filename(self, url: str) -> str:
        clean_url = url.split("?")[0].split("#")[0]
        name = clean_url.split("/")[-1] or "epg_source.xml"
        if name.endswith(".xml"):
            name = name[:-4]
        elif name.endswith(".xml.gz"):
            name = name[:-7]
        return sanitize_filename(name)

    def _format_timestamp(self, last_modified_header: Optional[str]) -> str:
        if last_modified_header:
            try:
                dt = parsedate_to_datetime(last_modified_header)
                return dt.strftime("%Y%m%d-%H%M%S")
            except Exception:
                pass
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")

    def download_plain_xml(self, url: str) -> Tuple[Path, bool]:
        base_prefix = self._get_base_filename(url)
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EPG-Filter Utility/1.0"}

        print(f"Downloading EPG source: {url}", flush=True)
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()

            last_mod = r.headers.get("Last-Modified")
            timestamp_str = self._format_timestamp(last_mod)

            target_filename = f"{base_prefix}_{timestamp_str}.xml"
            target_path = self.cache_dir / target_filename

            if target_path.exists():
                print(f"Cache hit: {target_filename} is up to date.", flush=True)
                return target_path, False

            for old_file in self.cache_dir.glob(f"{base_prefix}_*.xml"):
                try:
                    old_file.unlink()
                except Exception as e:
                    logger.warning(f"Could not remove stale cache file {old_file}: {e}")

            tmp_download = target_path.with_suffix(".tmp")
            if tmp_download.exists():
                tmp_download.unlink()

            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0

            with open(tmp_download, "wb") as f_out:
                for chunk in r.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    f_out.write(chunk)
                    downloaded += len(chunk)

            if tmp_download.exists():
                tmp_download.replace(target_path)

            print(f"Downloaded plain XML -> {target_filename} ({format_bytes(downloaded)})", flush=True)
            return target_path, True


def process_epg_sources(
    cached_files: List[Path],
    wanted_channels: Set[str],
    output_path: Path,
    match_by_name: bool = False,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_output_path = output_path.with_suffix(".tmp")

    if tmp_output_path.exists():
        tmp_output_path.unlink()

    seen_channels: Set[str] = set()
    seen_programmes: Set[Tuple[str, str, str]] = set()
    # When matching by name, the IDs we actually want to keep for programme
    # filtering are the source's OWN channel ids (resolved below) -- not the
    # wanted_channels strings themselves, which came from a different
    # provider's naming scheme and won't appear anywhere in this feed.
    resolved_ids: Set[str] = set()
    matched_wanted_names: Set[str] = set()

    if match_by_name:
        wanted_by_normalized = {}
        for w in wanted_channels:
            wanted_by_normalized.setdefault(normalize_channel_name(w), w)

    generator_name = (
        "epg.guru v1.4.0 (f64ebb9-dirty) - in memory of Jesse Mann, the epg guru himself. Rest in peace, friend."
    )
    utc_now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S +0000")

    total_wanted = len(wanted_channels)
    print(f"Loading channels (0% done)...", flush=True)

    with open(tmp_output_path, "wb") as xml_out:
        xml_out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        header_tag = f'<tv date="{utc_now}" generator-info-name="{generator_name}">\n'.encode("utf-8")
        xml_out.write(header_tag)

        for file_path in cached_files:
            try:
                with open(file_path, "rb") as stream:
                    context = etree.iterparse(stream, events=("start", "end"))
                    _, root = next(context)

                    for event, elem in context:
                        if event == "end":
                            if elem.tag == "channel":
                                ch_id = elem.get("id")
                                is_match = False

                                if match_by_name:
                                    if ch_id not in seen_channels:
                                        for dn in elem.findall("display-name"):
                                            norm = normalize_channel_name(dn.text or "")
                                            wanted_name = wanted_by_normalized.get(norm)
                                            if wanted_name:
                                                is_match = True
                                                matched_wanted_names.add(wanted_name)
                                                break
                                else:
                                    is_match = ch_id in wanted_channels

                                if is_match and ch_id not in seen_channels:
                                    seen_channels.add(ch_id)
                                    resolved_ids.add(ch_id)
                                    xml_bytes = etree.tostring(elem, encoding="utf-8", method="xml").strip()
                                    xml_out.write(xml_bytes + b"\n")

                                    pct = min(100, int((len(seen_channels) / total_wanted) * 100))
                                    if len(seen_channels) % 5 == 0 or pct == 100:
                                        print(f"Loading channels ({pct}% done)...", flush=True)
                                elem.clear()
                            root.clear()

                print(f"Channels loaded ({len(seen_channels)} channels).", flush=True)
                if match_by_name:
                    unmatched = sorted(set(wanted_channels) - matched_wanted_names)
                    if unmatched:
                        print(
                            f"WARNING: {len(unmatched)} wanted channel(s) had no name match in this "
                            f"source and will be missing from the output: {', '.join(unmatched)}",
                            flush=True,
                        )
                print(f"Loading programs (0% done progress bar)...", flush=True)

                effective_wanted = resolved_ids if match_by_name else wanted_channels

                prog_count = 0
                with open(file_path, "rb") as stream:
                    context = etree.iterparse(stream, events=("start", "end"))
                    _, root = next(context)

                    for event, elem in context:
                        if event == "end":
                            if elem.tag == "programme":
                                ch_id = elem.get("channel")
                                start = elem.get("start", "")
                                stop = elem.get("stop", "")
                                prog_key = (ch_id, start, stop)

                                if ch_id in effective_wanted and prog_key not in seen_programmes:
                                    seen_programmes.add(prog_key)
                                    xml_bytes = etree.tostring(elem, encoding="utf-8", method="xml").strip()
                                    xml_out.write(xml_bytes + b"\n")
                                    prog_count += 1

                                    if prog_count % 2000 == 0:
                                        pct = min(99, int((prog_count / (total_wanted * 250)) * 100))
                                        print(f"Loading programs ({pct}% done progress bar)...", flush=True)
                                elem.clear()
                            root.clear()

                print(f"Programs loaded ({len(seen_programmes)} programs).", flush=True)

            except Exception as e:
                logger.error(f"Error parsing EPG source {file_path.name}: {e}")

        xml_out.write(b"</tv>\n")
        xml_out.flush()

    if output_path.exists():
        output_path.unlink()

    tmp_output_path.replace(output_path)


def compress_output(xml_path: Path, gz_path: Path, keep_uncompressed: bool = True):
    tmp_gz_path = gz_path.with_suffix(".tmp")

    if tmp_gz_path.exists():
        tmp_gz_path.unlink()

    with open(xml_path, "rb") as f_in:
        with gzip.open(tmp_gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
            f_out.flush()

    if gz_path.exists():
        gz_path.unlink()

    tmp_gz_path.replace(gz_path)

    if not keep_uncompressed and xml_path.exists():
        xml_path.unlink()


def main():
    parser = argparse.ArgumentParser(description="Filter EPG feeds.")
    parser.add_argument("-i", "--input-list", required=True, help="Path to channel list file.")
    parser.add_argument("-o", "--output", default=None, help="Output destination path.")
    parser.add_argument("-f", "--force", action="store_true", help="Force download/rebuild.")
    parser.add_argument("-d", "--delete-uncompressed", action="store_true", help="Delete XML file after compressing to .gz")
    parser.add_argument("-c", "--cache-dir", default="./cache", help="Cache directory.")

    args = parser.parse_args()

    config_dir = Path("./config")
    cache_dir = Path(args.cache_dir)
    input_list_file = Path(args.input_list)

    if args.output is None:
        stem = input_list_file.stem
        if stem.endswith("_full"):
            stem = stem[:-5]
        output_xml = Path("./data") / f"{stem}.xml"
    else:
        output_xml = Path(args.output)

    if output_xml.suffix == ".gz":
        output_xml = output_xml.with_suffix("")

    output_gz = Path(f"{output_xml}.gz")

    wanted_channels, target_urls = load_aggregated_wanted_channels_and_sources(input_list_file)

    if not target_urls:
        urls_file = config_dir / "urls.txt"
        if urls_file.exists():
            target_urls = parse_config_file(urls_file)

    if not wanted_channels or not target_urls:
        print("Error: Invalid configuration or missing channels/sources.", flush=True)
        sys.exit(1)

    cache_mgr = CacheManager(cache_dir)
    cached_files = []
    any_updated = False

    for url in target_urls:
        try:
            cached_file, updated = cache_mgr.download_plain_xml(url)
            cached_files.append(cached_file)
            if updated:
                any_updated = True
        except Exception as e:
            print(f"Error downloading source: {e}", flush=True)

    if args.delete_uncompressed and output_xml.exists() and output_gz.exists() and not any_updated and not args.force:
        output_xml.unlink()
        print("Done. Output updated.", flush=True)
        sys.exit(0)

    if not args.force and not any_updated and output_gz.exists() and (not output_xml.exists() or not args.delete_uncompressed):
        print("Done. Output is up to date.", flush=True)
        sys.exit(0)

    process_epg_sources(
        cached_files,
        wanted_channels,
        output_xml,
        match_by_name=any("gracenote" in url.lower() for url in target_urls),
    )
    compress_output(output_xml, output_gz, keep_uncompressed=not args.delete_uncompressed)

    print(f"Done. Successfully saved to {output_gz.name}.", flush=True)


if __name__ == "__main__":
    main()
