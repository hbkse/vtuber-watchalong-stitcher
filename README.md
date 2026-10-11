# vtuber-watchalong-stitcher

Turn a VTuber anime watchalong (同時視聴) stream into a watchable video: the anime plays on
the left, the streamer on the right, synced to her reactions, with subtitles for both.

```
+-----------------------------+--------+
|  streamer subs              |        |
+-----------------------------+ stream |
|                             |  (her  |
|    anime (with its subs)    | model) |
|                             |        |
+-----------------------------+--------+
```

The output is the **whole stream** at the stream's resolution. While an episode plays you
get the split layout. If she pauses, the anime freezes on that frame. Between episodes (chatting,
superchats) the original stream is shown full-frame.

## Quick start

You only need Docker. Everything else (ffmpeg, yt-dlp, aria2, tesseract, Whisper) lives in the
image, so your machine stays clean.

1. Install a Docker runtime: [OrbStack](https://orbstack.dev) (lightest on macOS),
   [Docker Desktop](https://www.docker.com/products/docker-desktop/), or Docker Engine on Linux.
   - Give it plenty of CPU. Speech-to-text and encoding are CPU-bound.
2. Clone this repo and run:

```sh
./watchalong.sh https://www.youtube.com/live/eFYByyw5AJc \
  https://nyaa.si/view/1325663 https://nyaa.si/view/1329078 https://nyaa.si/view/1332706 \
  https://nyaa.si/view/1336130 https://nyaa.si/view/1339458 https://nyaa.si/view/1342987
```

The result lands in `data/output/<video-id>.mp4`. The first run builds the image, which takes a
few minutes. It also downloads the speech model (about 1.6 GB, cached in a Docker volume).

List anime sources **in the order she watches them**. A batch torrent or a folder counts as
all of its episodes, in filename order. Each source can be any of these:
- a nyaa.si page
- a `.torrent` URL
- a magnet link
- a local file or folder

### Job files: local files, mixed sources, several streams

Instead of arguments you can describe the work in a text file and run
`./watchalong.sh --job jobs/pekora-mushoku-tensei.txt`:

```
stream  https://www.youtube.com/live/eFYByyw5AJc
episode https://nyaa.si/view/1325663          # a link...
episode ~/anime/Mushoku Tensei - 02.mkv       # ...or a local file
episode ./mushoku-batch/                       # ...or a folder of episodes

stream  ~/vods/another-watchalong.mp4          # a second job in the same file
episode magnet:?xt=urn:btih:...
```

- Each `stream` line starts a job. The `episode` lines below it are that stream's episodes,
  in watch order.
- Relative paths are relative to the job file.
- Many releases open with a few seconds of distributor bumper (e.g. the Funimation logo)
  before the episode, while her timer starts at the episode itself. The bumper is detected
  automatically by comparing the starts of the episodes; it needs at least two episodes from the
  same release. To set it by hand, end an episode line with `skip=SECONDS`
  (`episode https://nyaa.si/view/1325663  skip=7`), or pass `--anime-skip SECONDS` for all
  episodes.
- `vocab` lines list names and words she uses, as `Japanese=English` (English optional),
  separated by commas: `vocab ぺこら=Pekora, 野うさぎ=nousagi`. See
  [Names and vocabulary](#names-and-vocabulary).
- Local streams and episodes skip downloading. If a local episode has a subtitle file next to
  it with the same name (`.ass`, `.srt`, `.vtt`), that file is used instead of the embedded subtitles.

- Any [option](#options) can be a line too, by its long name without the dashes:
  `ratio 0.22`, `timer-roi 1700,40,200,60`, `no-duck`. Lines before the first `stream` are
  defaults for every job in the file; lines inside a job apply to that job only.

`watchalong.sh` mounts your home folder (and `/Volumes`) read-only into the container at
the same paths, so local paths work as-is.

### Large batches

```
# batch.txt: defaults for every job below
translator   claude-code
cleanup      render

stream   https://www.youtube.com/live/eFYByyw5AJc
name     pekora-mushoku-s1e01-06        # output file name (default: the video id)
episode  https://nyaa.si/view/1325663
episode  https://nyaa.si/view/1329078
vocab    ぺこら=Pekora, 野うさぎ=nousagi

stream   https://www.youtube.com/live/AAAAAAAAAAA
name     marine-frieren-01
episode  ~/anime/Frieren/
ratio    0.22                           # this job only
timer-roi 1700,40,200,60
```

```sh
./watchalong.sh --job batch.txt --dry-run   # check every job first
./watchalong.sh --job batch.txt
./watchalong.sh --job jobs/                 # every *.txt in the folder, in name order
```

- **Precedence:** a job's own lines, then the file's defaults, then the command line. A flag
  on the command line is the fallback for every job.
- **Failures stay contained.** A job that fails (bad option, download error, no timer found) is
  logged and the next one starts. At the end a summary lists every job as done, skipped or
  failed with the reason, and the exit status is 1 if any failed. Each job's log is in
  `data/logs/<name>.log`.
- **Finished jobs are skipped.** A job is skipped when its output file already exists, so after
  fixing a failure you can re-run the same file. `--force` redoes them.
- **`--dry-run`** reports every problem before anything is downloaded: unknown keys, bad option
  values, missing local files, two jobs writing the same output file.
- **Disk:** each job keeps about 10 GB of downloads and caches unless `cleanup` says
  otherwise. `render` deletes the render pieces (the bulk of it); `downloads` also deletes the
  downloaded stream and episodes; `all` deletes the job's whole work folder, including the
  transcript and translation caches. Local files are never touched.
- **Unattended translation** with `claude-code` inside Docker needs `CLAUDE_CODE_OAUTH_TOKEN` in
  `.env` (see [Translation with Claude Code](#translation-with-claude-code)).

### Quick preview first

Rendering 2+ hours of 1080p60 takes a while. To check sync and layout on a few minutes
first:

```sh
./watchalong.sh <stream> <anime...> --range 0:15:00-0:18:00 --no-asr
```

Downloads, the timer scan, and transcripts are all cached in `data/work/<video-id>/`, so
re-running with different options only redoes what changed.

## Options

| Flag | Default | |
|---|---|---|
| `--job FILE\|DIR` | | Read streams and episodes from a job file, or every `*.txt` in a folder, instead of arguments. |
| `--dry-run` | | Check every job and exit without running anything. |
| `--force` | | Run jobs whose output already exists. |
| `--name` | video id | Output file name, without `.mp4`. |
| `--cleanup` | `none` | After a job succeeds, delete `render` pieces, `downloads` too, or `all` of its work folder. |
| `--ratio` | `0.25` | Share of the width for the streamer strip. Try 0.2–0.3. |
| `--crop-offset` | `0` | Shift the strip's crop right (+) or left (−) of center, in pixels. |
| `--range START-END` | whole stream | Render only part of the stream. |
| `--no-asr` | | Skip speech-to-text (no streamer subtitles). |
| `--no-render` | | Stop after writing her subtitles to `data/work/<id>/vtuber.ass`. Use it to check subs before a long render. |
| `--asr-model` | `large-v3-turbo` | Any faster-whisper model. `large-v3` is slower and a bit more accurate. |
| `--vocab TERMS` | | Names and words for the translator, e.g. `'ぺこら=Pekora, 野うさぎ=nousagi'`. Repeatable. |
| `--no-auto-vocab` | | Don't ask Claude Code for the anime's names and terms. |
| `--translator` | `none` | `claude-code` translates with your Claude Pro/Max subscription (see below). `none` burns her Japanese transcript. `anthropic`, `openai` and `gemini` are placeholders, not implemented yet. |
| `--anime-skip` | `auto` | Seconds of distributor bumper before each episode, or `auto` to detect it. |
| `--timer-roi X,Y,W,H` | auto | Timer position, if auto-detection fails. |
| `--render-jobs` | cores / 3 | Pieces rendered in parallel. |
| `--preset`, `--crf` | `veryfast`, `20` | x264 speed and quality. |
| `--anime-volume`, `--voice-volume` | `0.8`, `1.0` | Mix levels. |
| `--no-duck` | | Don't lower the anime while she talks. |
| `--cookies FILE` | | cookies.txt for members-only streams (put it under `data/`). |

### Names and vocabulary

Speech recognition can't know names it has never heard, so it writes them phonetically
(無職転生 came out as むしろくてんせえ). The vocabulary list is given to the translator as a
glossary, so it can recognize the misheard names and render them consistently.

It is deliberately not given to Whisper as hotwords: that made it hallucinate the words
into unrelated lines (ぺこいつの現実…) and, over a long stream, skip minutes of speech.

Sources, merged in order:
1. `vocab` lines in the job file. These are a good place for the streamer's own terms: her name,
   her fans' name, her catchphrases.
2. `--vocab` on the command line.
3. **Automatic:** if the `claude` CLI is logged in, Claude Code reads the anime's English
   subtitles once and lists its characters, places and terms with their Japanese spellings
   (cached in `data/work/<id>/vocab-auto-*.json`, which you can edit). Turn it off with
   `--no-auto-vocab`.

Changing the vocabulary re-runs translation only.

### Translation with Claude Code

`--translator claude-code` runs the Claude Code CLI (installed in the image) headless, so it
uses your Claude Pro/Max plan's usage instead of API billing. A 3-hour stream is about 15
requests. Each request also gets the anime's English subtitles from the same moment, so
character names come out right and short reactions make sense.

1. On your machine, run `claude setup-token` and copy the token.
2. Put it in `.env` (see `.env.example`): `CLAUDE_CODE_OAUTH_TOKEN=...`
3. Run with `--translator claude-code`. Optionally set `WATCHALONG_CLAUDE_MODEL=sonnet` or `opus`.

The translation is cached, so later re-renders don't use the subscription again.

## How it works

1. **Download.** yt-dlp fetches the stream (H.264, ≤1080p) and aria2 fetches the torrents.
2. **Anime subtitles.** The English ASS track and its embedded fonts are extracted from each MKV.
   - Distributor bumpers at the start of each episode are found by comparing episodes: frames shared
     with other episodes' openings are bumper, and the episode starts at its first unique frame.
3. **Sync.** The stream shows an `HH:MM:SS` timer that counts up from zero for each episode.
   - The timer is located by OCR'ing frames across the stream.
   - That region is then read about twice a second for the whole stream with tesseract.
   - From the readings, the timeline is built:
     - **play:** the timer ticks, so `anime time = stream time − offset`.
     - **pause:** the timer is frozen mid-episode.
     - **new episode:** the timer resets to zero.
     - **full:** anything else.
   - Offsets come out with sub-second precision (see `watchalong/align.py`).
   - The number of episodes found must match the number of anime files. If it doesn't, the run
     stops and prints what it found, so it never produces a silently wrong video.
4. **Speech-to-text.** faster-whisper transcribes her voice (Japanese) on the CPU, with voice
   activity detection, in independent ~5 minute chunks cut at quiet moments (one long pass
   can derail and drop minutes of speech; chunks also make an interrupted run resumable).
   - Subtitle lines are timed from **word-level timestamps**, so a line appears when she starts
     talking and disappears about 0.4 s after she stops.
   - Long lines split at punctuation.
   - The raw word timings are cached, so line timing can be retuned (`watchalong/asr.py`)
     without transcribing again.
5. **Translation.** This goes through a provider interface (`watchalong/translate.py`). Lines are sent in batches with the anime dialogue on screen at the time as context.
6. **Render.**
   - The anime subs are burned at the anime's native resolution, so fansub typesetting survives.
   - The anime is then scaled into the panel, the strip is cropped from the center of the stream,
     and her subs are burned on top.
   - Audio mixes her voice and the anime, ducking the anime while she talks.
   - Rendering happens in pieces of at most 5 minutes that are joined losslessly, so an interrupted
     render resumes.

## Dependencies

Everything is installed in the Docker image (`Dockerfile`):

| What | Why |
|---|---|
| Python 3.12 (`python:3.12-slim-trixie`) | Runtime. |
| ffmpeg with libass (Debian trixie) | Decoding, compositing, subtitle burning, encoding. |
| yt-dlp + Deno | YouTube download. Deno solves YouTube's JS challenges. |
| aria2 | BitTorrent downloads. |
| tesseract-ocr | Reading the on-screen timer. |
| faster-whisper (CTranslate2) | Japanese speech-to-text. |
| fonts-noto-cjk | Font for Japanese subtitles. |

Python packages are listed in `pyproject.toml`. To pick up a newer yt-dlp after YouTube
changes something, run `docker compose build --no-cache`.

### Running without Docker

You need Python ≥ 3.11 and the tools above on your `PATH`. Note that ffmpeg must be built with
libass. On macOS Homebrew that means `ffmpeg-full`, not `ffmpeg`. Then:

```sh
pip install -e .
watchalong <stream> <anime...>
```

## Performance

Expect the following on a recent 10-core laptop inside Docker (CPU only; Docker on macOS can't use the GPU):

- Timer scan: about 5–15 min.
- Speech-to-text: about 20–40 min for a 3-hour stream with `large-v3-turbo`.
- Render: several times faster than real-time with `--preset veryfast` (pieces render in parallel).

## Development

```sh
python -m unittest discover tests   # alignment logic, no external tools needed
```

## Notes

- **Personal use only.** The output contains the anime itself, so don't upload it.
- **Members-only streams:** export your YouTube cookies to `data/cookies.txt` and pass
  `--cookies /data/cookies.txt`.
- **Changing an episode's source** (e.g. a torrent with no seeders): edit the link and re-run.
  That episode is re-downloaded. Everything else stays cached.
