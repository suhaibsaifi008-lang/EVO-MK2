"""YouTube: transcript fetch + summarization.

youtube_summarize pulls the video's captions (no download, no API key),
map-reduces them through the primary model and hands you a real summary.
Optional save=true drops it into the vault.
"""
import json
import re

from . import db
from .tools import tool

CHUNK_CHARS = 11000


def _video_id(source: str) -> str | None:
    source = (source or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})", source)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", source):
        return source
    return None


def _fetch_transcript(video_id: str) -> str:
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "hi", "en-IN"])
    except Exception:
        fetched = api.fetch(video_id)  # any available language
    parts = [snip.text.strip() for snip in fetched]
    return " ".join(p for p in parts if p)


def _summarize_chunks(transcript: str, style: str) -> str:
    from .llm import chat

    if len(transcript) <= CHUNK_CHARS:
        return chat(
            [{"role": "system",
              "content": ("Summarize this video transcript. " + style +
                          " Use short markdown bullets. Max 220 words.")},
             {"role": "user", "content": transcript}],
            temperature=0.2, timeout=60)
    chunks = [transcript[i:i + CHUNK_CHARS]
              for i in range(0, len(transcript), CHUNK_CHARS)][:6]
    partials = []
    for i, ch in enumerate(chunks):
        partials.append(chat(
            [{"role": "system",
              "content": ("Condense this PART of a video transcript into "
                          "5-8 key bullet points. Preserve names/numbers.")},
             {"role": "user", "content": ch}],
            temperature=0.2, timeout=60))
    merged = "\n\n".join(f"[Part {i+1}]\n{p}" for i, p in enumerate(partials))
    return chat(
        [{"role": "system",
          "content": ("Merge these part-summaries of one video into a single "
                      "coherent summary. " + style +
                      " Dedupe, keep the best details, max 300 words.")},
         {"role": "user", "content": merged[:12000]}],
        temperature=0.3, timeout=60)


@tool("youtube_summarize", "Fetch a YouTube video's transcript and summarize it. Optionally save the summary to the vault.",
      {"url": {"type": "string"},
       "style": {"type": "string"},
       "save": {"type": "boolean"}},
      permission="read")
def youtube_summarize(url: str, style: str = "", save: bool = False) -> dict:
    vid = _video_id(url)
    if not vid:
        return {"ok": False,
                "speech": "That doesn't look like a YouTube link or video ID.",
                "data": {}}
    from .tools import emit_progress

    emit_progress("fetching transcript...")
    try:
        transcript = _fetch_transcript(vid)
    except Exception as exc:
        msg = str(exc)[:150]
        return {"ok": False,
                "speech": ("No usable captions for that video "
                           f"({msg}). If it has none, I can't summarize it."),
                "data": {}}
    if len(transcript) < 80:
        return {"ok": False,
                "speech": "The captions were too sparse to summarize.",
                "data": {}}
    emit_progress("summarizing...")
    style_line = f"Focus: {style}." if style else ""
    summary = _summarize_chunks(transcript, style_line)
    head = f"YouTube {vid}"
    out = {"ok": True, "speech": summary[:900],
           "data": {"video_id": vid, "transcript_chars": len(transcript),
                    "summary": summary}}
    if save:
        try:
            from .vault import write_note

            path = write_note(f"yt {vid}",
                              f"# {head}\n\n{summary}\n",
                              tags=["youtube"])
            out["data"]["saved"] = str(path)
            out["speech"] += " Saved to the vault."
        except Exception:
            pass
    # Phase 7.6: video knowledge joins the graph
    try:
        from . import rag as _rag

        _rag.kg_extract(summary, source="youtube")
    except Exception:
        pass
    return out


@tool("youtube_transcript", "Get the raw caption text of a YouTube video.",
      {"url": {"type": "string"}}, permission="read")
def youtube_transcript_tool(url: str) -> dict:
    vid = _video_id(url)
    if not vid:
        return {"ok": False, "speech": "Not a YouTube link.", "data": {}}
    try:
        text = _fetch_transcript(vid)
    except Exception as exc:
        return {"ok": False, "speech": f"No captions ({str(exc)[:120]})",
                "data": {}}
    return {"ok": True, "speech": text[:500],
            "data": {"video_id": vid, "text": text[:8000]}}
