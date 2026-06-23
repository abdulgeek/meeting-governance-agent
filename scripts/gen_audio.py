"""Build the meeting audio with macOS `say` + ffmpeg.

We make our own audio (the brief asks for that) and give each person their own voice.
That's the cheap trick that lets us skip speaker detection entirely: the speaker is
just which file the line came from, so the consent gate is exact.

Outputs:
  audio/uNN_<speaker>.wav   one 16 kHz mono file per line
  audio/manifest.json       [{idx, speaker, audio_file}] - this is all the agent reads
  audio/meeting_full.wav    everything stitched together, for listening / the Loom

Note the manifest has no text and no expected answer in it on purpose - the pipeline
has to transcribe the audio itself, it can't peek.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "audio"

# different accents/genders so the four speakers are easy to tell apart in the demo
VOICES = {"maya": "Karen", "raj": "Aman", "lena": "Moira", "tomas": "Daniel"}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> None:
    script = json.loads((ROOT / "meeting" / "meeting_script.json").read_text())
    AUDIO_DIR.mkdir(exist_ok=True)
    manifest = []
    concat_lines = []

    for utt in script["utterances"]:
        idx, speaker, text = utt["idx"], utt["speaker"], utt["text"]
        voice = VOICES[speaker]
        aiff = AUDIO_DIR / f"u{idx:02d}_{speaker}.aiff"
        wav = AUDIO_DIR / f"u{idx:02d}_{speaker}.wav"

        # say writes aiff; ffmpeg standardises to 16 kHz mono, which is what whisper wants
        run(["say", "-v", voice, "-o", str(aiff), text])
        run(["ffmpeg", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)])
        aiff.unlink(missing_ok=True)

        manifest.append({"idx": idx, "speaker": speaker, "audio_file": f"audio/{wav.name}"})
        concat_lines.append(f"file '{wav.name}'")
        print(f"  [{idx:>2}] {speaker:<6} {voice:<7} -> {wav.name}")

    (AUDIO_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # one combined file, handy for the demo / Loom
    concat_file = AUDIO_DIR / "_concat.txt"
    concat_file.write_text("\n".join(concat_lines) + "\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", str(AUDIO_DIR / "meeting_full.wav")])
    concat_file.unlink(missing_ok=True)

    print(f"\nWrote {len(manifest)} segments + manifest.json + meeting_full.wav to {AUDIO_DIR}")


if __name__ == "__main__":
    main()
