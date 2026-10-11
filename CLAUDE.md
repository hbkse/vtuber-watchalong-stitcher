# vtuber-watchalong-stitcher: technical notes

User-facing docs (modes, options, troubleshooting) are in README.md. This file covers how the
pipeline works, why it's built the way it is, and how to work on it.

## Layout

| Module | Role |
|---|---|
| `cli.py` | Argument parser, batch runner (`main` → `plan_jobs` → `run_job` per job → `summary`), cache keys, cleanup. `streamer_subs`, `auto_vocab` and `anime_skips` glue the ASR/translation/preroll steps together. |
| `jobs.py` | Job file parser. Collects every error in a file before raising. |
| `download.py` | Sources for stream and episodes: local file/folder, torrent (aria2c), or any other http(s) link (yt-dlp). `download_anime` returns one list of videos per source. |
| `extract.py` | Pulls the English text subtitle track and attached fonts out of each episode (or a sidecar `.ass/.srt/.vtt`). |
| `preroll.py` | Detects distributor bumpers at the start of episodes. |
| `timer.py` | Finds the on-screen timer and OCRs it across the stream (tesseract, multiprocess). |
| `align.py` | Turns timer readings into a timeline of `play` / `pause` / `full` intervals. |
| `asr.py` | faster-whisper transcription and subtitle line building from word timestamps. |
| `translate.py` | Translator providers (`none`, `claude-code`; API providers are stubs), anime-dialogue context. |
| `subs.py` | Writes the streamer's ASS file, placing each line for the layout on screen at the time. |
| `layout.py` | Geometry of the split layout. |
| `render.py` | ffmpeg compositing in pieces, concat, audio mix. |
| `media.py`, `util.py` | ffprobe, frame iteration, logging, JSON helpers, time parsing. |

## Pipeline

1. **Download.** Torrent sources (nyaa page → its `.torrent`, `.torrent` URL, magnet) go through
   aria2c; other http(s) links through yt-dlp, preferring H.264 ≤1080p (decodes much faster than
   VP9/AV1, and the stream is decoded several times). An episode playlist link downloads every
   entry, prefixed with its index so natural sort keeps playlist order. A stream torrent must
   contain exactly one video. Downloads go to `data/work/<id>/stream/` and `anime-src/NN/`
   (`_download`); a `.done` file records the source, so changing a link re-downloads only that
   one. Work dirs are named by YouTube id, `nyaa-<id>`, local file stem, or a URL hash.
2. **Anime subtitles** → `anime/NN/subs.ass` + `fonts/`: a sidecar file, else the first suitable
   text track (burned in at render time). No track means the subs are assumed burned into the
   picture already (YouTube uploads, hardsubs); the episode renders as-is and contributes no
   dialogue to the translation context.
3. **Bumpers** (`preroll.py`). Fingerprints the first 30 s of each episode at 10 fps as 32×18
   grayscale frames. A frame "matches" if its mean abs difference to any frame of another
   episode's start is < 12; flat frames (std < 12: black, fades) are ignored. The cut is the
   changepoint maximizing (matched − unmatched) frames before it, which tolerates re-encodes
   that differ during fast animation. Needs ≥ 1 s of evidence and two episodes of a release;
   > 20 s is treated as a shared OP and not skipped. Cached in `anime/preroll.json`.
   Precedence: job file `skip=` > `--anime-skip` > detection.
4. **Timer.** Detection looks for 1 Hz ticking regions at low resolution, then OCR runs at
   ~1.9 samples/s over the whole stream in worker processes. Cached in `timer_readings.json`.
5. **Alignment** (`align.py`). Runs where `stream_time − timer` is constant (±1.2 s) are play;
   a frozen timer is a pause; a reset near 0 is a new episode. The episode count must equal the
   number of anime files or the run aborts: never render a silently wrong video. Episode
   durations passed in are minus the bumper, then each segment's `anime_start` is shifted by it.
   Result: `timeline.json`.
6. **ASR** (`asr.py`), see below.
7. **Translation** (`translate.py`), see below.
8. **Subtitles** (`subs.py`). Each line is split at layout changes (`_placements`): the "Watch"
   style puts it in the top band above the anime, "Full" at the bottom of the full-frame stream.
   Empty translations are skipped.
9. **Render** (`render.py`). The timeline is cut into pieces of ≤ 5 min; each piece is one ffmpeg
   run. Anime subs are burned at the anime's native resolution (so fansub typesetting survives),
   then the anime is scaled into the panel; the strip is a center crop of the stream; streamer
   subs are burned last. Pieces run in parallel (`cores / 3` jobs, x264 threads split between
   them), are joined with the concat demuxer (`-c copy`), then muxed with one audio track mixed in
   a single pass (voice + anime, anime ducked with `sidechaincompress`). Final mux uses
   `-map_chapters -1` (otherwise a stray chapters data track appears) and `+faststart`.

## ASR design

- faster-whisper `large-v3-turbo`, CPU int8, `beam_size=5`, VAD on,
  `condition_on_previous_text=False`, `word_timestamps=True`.
- Audio is decoded by ffmpeg to 16 kHz f32 numpy, not by faster-whisper: its PyAV decoding broke
  with PyAV 19 (`open() got an unexpected keyword argument 'metadata_errors'`).
- **Transcribed in ~5 min chunks**, each cut at the quietest 0.5 s within ±20 s of the mark
  (`_chunk_bounds`), cached per chunk in `asr-v<VERSION>-<model>-chunks/`. One pass over 2h43m
  derailed and produced nothing for 3 minutes of continuous speech; chunks bound the damage and
  make transcription resumable.
- **No hotwords.** Passing the vocabulary as `hotwords` injected it into unrelated lines
  (ぺこいつの現実 for こいつの剣術), made some chunks 4× slower, and contributed to the dropout.
  The vocabulary only goes to the translator.
- Raw segments with word timings are cached; `lines_from_segments` builds lines from them, so line
  timing can be tuned without re-transcribing:
  - Cut at segment ends, at pauses ≥ 2 s, and at pauses ≥ 0.7 s after punctuation; never before
    a character that can't start a word (small kana, ー, ん, punctuation).
  - Whisper sometimes misplaces a word by seconds (なん … の魔法これ, さっきのや … つ). A long
    pause inside a segment doesn't cut off a ≤ 2-character piece; `_span` trims misplaced or
    stretched edge words when timing the line; `_move_fragments` moves a dangling single
    character across a pause onto the speech it belongs to.
  - Lines over 5 s or 24 characters split at the punctuation nearest the middle (or the biggest
    pause if over 10 s), measured on the trimmed span.
  - A line shows 0.4 s past the last word, at least 1 s, never overlapping the next.
- Bump `asr.VERSION` when the cached transcript format changes.

## Translation

- `claude-code` runs `claude -p` headless (covered by a Pro/Max plan): batches of 100 lines with
  10 lines of overlap for context, 4 in parallel, 3 attempts each, JSON-array output validated
  per batch. Model from `--translate-model` (passed to `claude --model`; part of `cache_key`).
- `--subs` picks the mode: `none` skips ASR and translation; `japanese` uses the passthrough
  translator (name `none`) and skips vocabulary entirely (it only feeds the translator);
  `english` uses `--translator`. Placeholder providers (`implemented = False`) are hidden from
  the `--translator` choices.
- Each batch's prompt has the stream title, the glossary, the preceding lines, and the anime's
  English dialogue from [batch start − 20 s, batch end + 5 s] mapped to stream time via the
  timeline (`anime_dialogue`; signs/typesetting with `\p1` or `\pos` are skipped).
- Auto vocabulary: one `claude -p` call reads the anime subs and returns up to 40 `{ja, en}`
  pairs, cached in `vocab-auto-<key>.json` (editable). Failure only logs a warning.
- **Docker auth:** the container can't see the macOS keychain login, so `claude` inside Docker
  needs `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` in `.env`. Without it, the workaround
  used during development was running `streamer_subs` on the host against Docker's caches, then
  rendering in Docker (which picks up the cached translation).

## Caching

Everything lives in `data/work/<stream-id>/`, and every expensive step is keyed on what affects
it (`cli._key` = sha1 of the JSON of its inputs, 10 chars):

| File | Keyed on |
|---|---|
| `timer_readings.json`, `timeline.json` | — (delete to redo) |
| `asr-v<VERSION>-<model>[-range].json` + `-chunks/` | ASR version, model, range |
| `translation-<provider>-<key>.json` | lines, provider's `cache_key()` (model, prompt, batching), vocabulary |
| `vocab-auto-<key>.json` | stream title, anime subs |
| `chunks-<key>/`, `audio-<key>-*.m4a` | render inputs and settings |

The translation key does not include the anime-dialogue context, so fixing sync (e.g. bumpers)
reuses an old translation until the lines or vocabulary change.

## Batch runner

- Job files: `stream`, `episode` (+ optional trailing `skip=SECONDS`), `vocab`/`names`, and any
  CLI long option without dashes (`job_file_options` derives the allowed keys from the parser;
  `job`, `dry-run`, `help`, `vocab` excluded). Keys before the first `stream` are file defaults.
- Per job, options are applied by re-parsing `defaults + job options + argv` with the real
  parser (later wins: an explicit command-line flag beats the file, so the example file can
  list every default without making flags useless), so values get the same validation and
  types as the CLI. `_Parser.strict`
  makes parse errors raise `OptionError` instead of exiting.
- Plans fail individually: file parse errors, option errors, duplicate output paths, missing
  `claude` CLI. Jobs whose output exists are skipped unless `--force`. Each running job catches
  `SystemExit` and `Exception` (not `KeyboardInterrupt`), logs to `data/logs/<name>.log`, and the
  summary exits 1 if anything failed.
- `--cleanup` only ever deletes inside the job's work folder.

## Development

The host has none of the dependencies; run things in the image. Tests:

```sh
docker compose run --rm -v $PWD:/repo -w /repo --entrypoint sh watchalong \
  -c "pip install -q pytest && python -m pytest -q tests"
```

Experiments on real data (e.g. transcribing one chunk with different settings): mount the repo
at `/repo` and run a script with `--entrypoint python`; `data/` is at `/data`.

Native (no Docker) setup per OS is in README.md, "Without Docker". The `ffmpeg` on PATH must
have the `subtitles` filter (libass); the CLI checks this on startup.

## Gotchas

- `OMP_THREAD_LIMIT=1` is set only inside the OCR worker processes; set globally it pinned
  CTranslate2 (Whisper) to one core.
- macOS sleep pauses Docker (OrbStack); long runs need `caffeinate -s`.
- `watchalong.sh` bind-mounts, read-only at the same path, the folder of every local path in
  the arguments and job files (stream/episode/cookies lines, job files, `--cookies`), so local
  files work anywhere without exposing the whole home folder. Folders, not files, so sidecar
  subtitles are visible. It skips the value after each option that takes one; its list of
  value-less flags must match the parser's (`test_wrapper_knows_which_options_take_no_value`).
  `~` in job files is expanded with `WATCHALONG_HOST_HOME` inside the container (`HOME` there is
  `/root`, where the model cache lives).
- The Whisper model lives in the compose volume `watchalong-whisper-models` (fixed `name:` in
  `compose.yaml`; the default name would follow the folder name and orphan it on a rename).
- `example-job.txt` must stay valid and its listed values must equal the parser defaults;
  `tests/test_batch.py` checks both. Update it when adding or changing an option.
- New yt-dlp after YouTube changes: `docker compose build --no-cache`.
- The output is for personal use; it contains the anime.
