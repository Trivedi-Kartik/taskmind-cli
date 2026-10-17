"""Voice input and output, free by default.

Speech to text: Groq Whisper (free tier), or OpenAI Whisper with an OpenAI key.
Text to speech: Groq Orpheus (needs a one-time terms acceptance in the Groq
console), falling back to the computer's own offline voice (spd-say, espeak,
macOS `say`, Windows speech). Recording and playback use the system's audio
tools (arecord/aplay on Linux, sox or ffmpeg elsewhere), so nothing needs installing
on most Linux desktops.
"""

import http.client
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from taskmind import config

GROQ_URL = "https://api.groq.com/openai/v1"
OPENAI_URL = "https://api.openai.com/v1"
STT_MODELS = {"groq": "whisper-large-v3-turbo", "openai": "whisper-1"}
TTS_MODEL = "canopylabs/orpheus-v1-english"
TTS_VOICES = ["autumn", "diana", "hannah", "austin", "daniel", "troy"]
TTS_TERMS_URL = "https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english"
TTS_MAX_CHARS = 190  # Groq asks for under 200 characters per request
MAX_RECORD_SECONDS = 120
OUTPUT_MODES = ("auto", "groq", "local", "off")


class VoiceError(Exception):
    pass


# Recording and playback

def recorder_command(path, seconds=MAX_RECORD_SECONDS):
    """A command that records 16 kHz mono WAV from the default microphone, or None."""
    path = str(path)
    if shutil.which("arecord"):
        return ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", "-d", str(seconds), path]
    if shutil.which("rec"):
        return ["rec", "-q", "-r", "16000", "-c", "1", path, "trim", "0", str(seconds)]
    if shutil.which("ffmpeg"):
        # Windows needs a named DirectShow device, so ffmpeg recording is macOS/Linux only.
        source = {"Darwin": ["-f", "avfoundation", "-i", ":0"], "Linux": ["-f", "alsa", "-i", "default"]}
        if platform.system() in source:
            return ["ffmpeg", "-loglevel", "error", "-y", *source[platform.system()],
                    "-ac", "1", "-ar", "16000", "-t", str(seconds), path]
    return None


def player_command(path):
    for cmd in (["aplay", "-q"], ["paplay"], ["afplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(cmd[0]):
            return [*cmd, str(path)]
    return None


def local_tts_command(text):
    """The computer's own offline voice: (command, extra env) or None.

    "--" ends option parsing, so text starting with "-" is spoken, never read as a flag.
    """
    if shutil.which("say"):
        return ["say", "--", text], {}
    if shutil.which("spd-say"):
        return ["spd-say", "--wait", "--", text], {}
    for name in ("espeak-ng", "espeak"):
        if shutil.which(name):
            return [name, "--", text], {}
    if platform.system() == "Windows" and shutil.which("powershell"):
        script = ("Add-Type -AssemblyName System.Speech; "
                  "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:TM_SAY)")
        return ["powershell", "-NoProfile", "-Command", script], {"TM_SAY": text}
    return None


def record(wait_for_stop, path=None) -> Path:
    """Record until wait_for_stop() returns (e.g. the user presses Enter)."""
    path = Path(path or Path(tempfile.gettempdir()) / f"taskmind-{uuid.uuid4().hex}.wav")
    cmd = recorder_command(path)
    if not cmd:
        raise VoiceError("No audio recorder found. Install alsa-utils (Linux), or sox / ffmpeg (macOS).")
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(0.2)
        if proc.poll() is not None:
            detail = proc.stderr.read().decode(errors="replace").strip()
            raise VoiceError(f"Could not start recording: {detail or 'no microphone?'}")
        try:
            wait_for_stop()
        finally:
            if proc.poll() is None:
                # SIGINT lets the recorder finish the WAV file cleanly.
                proc.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if not path.exists() or path.stat().st_size < 4000:  # ~0.1 s of audio
            raise VoiceError("Nothing was recorded. Check your microphone and try again.")
    except BaseException:
        # Errors and Ctrl+C never leave a recording behind.
        path.unlink(missing_ok=True)
        raise
    return path


def play(path):
    cmd = player_command(path)
    if not cmd:
        raise VoiceError("No audio player found (aplay, paplay, afplay or ffplay).")
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Cloud speech APIs (stdlib only)

def _http(url, api_key, data, content_type, timeout=60):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": content_type,
               "User-Agent": "taskmind/0.1"}  # Groq's firewall blocks Python's default agent
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        if "model_terms_required" in detail:
            raise VoiceError(f"Groq's voice model needs a one-time terms acceptance: {TTS_TERMS_URL}") from err
        if err.code == 401:
            raise VoiceError("The API key was rejected. Run `tm setup`.") from err
        if err.code == 429:
            raise VoiceError("Voice rate limit reached. Try again shortly.") from err
        raise VoiceError(f"Speech service error {err.code}: {detail[:200]}") from err
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        raise VoiceError(f"Could not reach the speech service: {getattr(err, 'reason', err)}") from err


def multipart(fields: dict, file_field, filename, content: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def stt_provider(cfg):
    """(name, base_url, api_key, model) for speech to text, preferring free Groq."""
    for name, url in (("groq", GROQ_URL), ("openai", OPENAI_URL)):
        key = config.llm_settings(cfg, name)["api_key"]
        if key:
            return name, url, key, STT_MODELS[name]
    return None


def transcribe(cfg, path) -> str:
    provider = stt_provider(cfg)
    if not provider:
        raise VoiceError("Voice input needs a Groq key (free) or an OpenAI key. Run `tm setup`.")
    _, url, key, model = provider
    body, ctype = multipart({"model": model, "response_format": "json"}, "file", Path(path).name,
                            Path(path).read_bytes())
    text = json.loads(_http(f"{url}/audio/transcriptions", key, body, ctype)).get("text", "").strip()
    return text if re.search(r"\w", text) else ""


def chunks(text, limit=TTS_MAX_CHARS) -> list:
    """Split text into pieces under the TTS limit, at sentence (then comma, then word) breaks."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if len(sentence) > limit and current:
            pieces.append(current)
            current = ""
        while len(sentence) > limit:
            cut = max(sentence.rfind(", ", 0, limit), sentence.rfind(" ", 0, limit))
            cut = cut if cut > 0 else limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].lstrip(", ")
        if len(current) + len(sentence) + 1 <= limit:
            current = f"{current} {sentence}".strip()
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def speakable(text) -> str:
    """Turn terminal text into something pleasant to hear."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\bTM-(\d+)\.(\d+)\b", r"task \1, step \2", text, flags=re.I)
    text = re.sub(r"\bTM-(\d+)\b", r"task \1", text, flags=re.I)
    number = r"(\d+(?:[.,]\d+)*)"  # 1,500 or 2.5, but not a trailing comma
    text = re.sub(r"\$\s?" + number, r"\1 dollars", text)
    text = re.sub(r"₹\s?" + number, r"\1 rupees", text)
    text = re.sub(r"#(\d+)", r"number \1", text)
    for symbol, word in ((">=", " at least "), ("<=", " at most "), (">", " greater than "),
                         ("<", " less than "), ("=", " equals "), ("@", " at ")):
        text = text.replace(symbol, word)
    # Keep letters (with their combining marks, e.g. Devanagari vowel signs), digits and
    # light punctuation; drop emoji, markdown and other symbols.
    text = "".join(c if unicodedata.category(c)[0] in "LMN" or c.isspace() or c in ".,!?;:'()/%&+-" else " "
                   for c in text)
    return re.sub(r"\s+", " ", text).strip()


def synthesize(api_key, text, voice, out_dir) -> list:
    files = []
    for i, piece in enumerate(chunks(text)):
        audio = _http(f"{GROQ_URL}/audio/speech", api_key,
                      json.dumps({"model": TTS_MODEL, "input": piece, "voice": voice,
                                  "response_format": "wav"}).encode(), "application/json")
        path = Path(out_dir) / f"part{i}.wav"
        path.write_bytes(audio)
        files.append(path)
    return files


class Speaker:
    """Speaks text with the best available engine, falling back quietly and explaining once."""

    def __init__(self, cfg, notify=print):
        voice_cfg = cfg.get("voice") or {}
        self.mode = voice_cfg.get("output", "auto")
        self.voice = voice_cfg.get("name") or "autumn"
        self.groq_key = config.llm_settings(cfg, "groq")["api_key"]
        self.notify = notify
        self.engine = self._pick()

    def _pick(self):
        if self.mode == "off":
            return None
        if self.mode in ("auto", "groq") and self.groq_key and player_command("x.wav"):
            return "groq"
        if self.mode in ("auto", "local") and local_tts_command("x"):
            return "local"
        return None

    def describe(self) -> str:
        return {"groq": f"Groq Orpheus ({self.voice})", "local": "this computer's offline voice",
                None: "off (text only)"}[self.engine]

    def say(self, text):
        text = speakable(text)
        if not text or not self.engine:
            return
        remaining = chunks(text)
        if self.engine == "groq":
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    while remaining:
                        # One piece at a time, so a failure doesn't repeat what was already said.
                        play(synthesize(self.groq_key, remaining[0], self.voice, tmp)[0])
                        remaining.pop(0)
                return
            except VoiceError as exc:
                self.engine = "local" if self.mode == "auto" and local_tts_command("x") else None
                self.notify(f"Voice: {exc} Using {self.describe()} for now.")
        if self.engine == "local" and remaining:
            cmd, env = local_tts_command(" ".join(remaining))
            try:
                subprocess.run(cmd, env={**os.environ, **env}, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            except subprocess.TimeoutExpired:
                pass
