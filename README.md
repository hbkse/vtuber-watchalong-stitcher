# vtuber-watchalong-stitcher

Turn a VTuber anime watchalong (同時視聴) stream into a video you can actually watch: the
anime plays on the left, the streamer on the right, in sync with her reactions, with
subtitles for both.

```
+-----------------------------+--------+
|  her lines (subtitled)      |        |
+-----------------------------+ stream |
|                             |  (her  |
|    anime (with its subs)    | model) |
|                             |        |
+-----------------------------+--------+
```

You get the **whole stream** back. While an episode plays you see the layout above; if she
pauses, the anime freezes too. Before, between and after episodes (chatting, superchats) the
stream is shown full-screen with her subtitles at the bottom.

It works with streams that show an on-screen episode timer (`00:12:34` counting up), which
most watchalongs do. That timer is how the anime gets synced.

## 1. Install

Pick one.

### Docker (recommended)

1. Install Docker: [OrbStack](https://orbstack.dev) (lightest on macOS),
   [Docker Desktop](https://www.docker.com/products/docker-desktop/), or Docker Engine on
   Linux. Give it as many CPU cores as you can.
2. Clone this repo.

Everything else (ffmpeg, downloaders, OCR, speech recognition) is inside the image, which
builds itself on the first run: a few minutes, plus a 1.6 GB speech model download. You run
the program as `./watchalong.sh`.

### Everything on your machine

You need:

| Tool | Used for |
|---|---|
| Python 3.11 or newer | The program itself. |
| ffmpeg **built with libass** | Video work and burning in subtitles. |
| aria2 | Torrent downloads. |
| tesseract | Reading the on-screen timer. |
| Deno | Lets yt-dlp get past YouTube's player checks. |
| Noto Sans CJK font | Japanese text in subtitles. |

**macOS (Homebrew):**

```sh
brew install ffmpeg-full aria2 tesseract deno
brew install --cask font-noto-sans-cjk
# ffmpeg-full isn't put on your PATH automatically, and the plain `ffmpeg` formula has no
# libass. Add this to your shell profile:
export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"
```

**Debian / Ubuntu:**

```sh
sudo apt install ffmpeg aria2 tesseract-ocr fonts-noto-cjk python3-venv
curl -fsSL https://deno.land/install.sh | sh
```

Windows isn't tested; use WSL with the Debian/Ubuntu steps.

Then, in the repo folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

You run the program as `watchalong` (activate the venv first in each new terminal). It
takes exactly the same arguments and job files as `./watchalong.sh`. If a tool is missing it
tells you which one on startup.

### English subtitles: Claude Code

English translation uses [Claude Code](https://claude.com/claude-code) with your Claude Pro
or Max plan; nothing is billed separately.

- **Docker:** run `claude setup-token` on your machine and put the token in a file named
  `.env` in the repo folder (see `.env.example`):
  ```
  CLAUDE_CODE_OAUTH_TOKEN=...
  ```
- **On your machine:** install Claude Code and log in as usual.

Without Claude Code, use `subs japanese` (her transcript) or `subs none`.

## 2. Run

Results land in `data/output/`. There are two ways to say what to make.

### On the command line

The stream first, then the episodes **in the order she watches them**, then any settings:

```sh
./watchalong.sh https://www.youtube.com/live/eFYByyw5AJc \
  https://nyaa.si/view/1325663 https://nyaa.si/view/1329078 \
  --subs japanese --ratio 0.22
```

### With a job file (recommended)

A text file with the stream, its episodes and your settings. Easier to edit and re-run,
and it can hold many streams.

```sh
cp example-job.txt jobs/my-show.txt     # every setting, with its default, explained
# edit jobs/my-show.txt
./watchalong.sh --job jobs/my-show.txt
```

[`example-job.txt`](example-job.txt) lists every setting with its default value and a short
explanation; change what you need. The format:

```
subs     english                  # settings at the top apply to every stream in the file

stream   https://www.youtube.com/live/eFYByyw5AJc
episode  https://nyaa.si/view/1325663
episode  ~/anime/Mushoku Tensei - 02.mkv
vocab    ぺこら=Pekora, 野うさぎ=nousagi

stream   ~/vods/another-watchalong.mp4    # a second stream
episode  ~/anime/Some Show/
ratio    0.22                             # a setting for this stream only
```

- Each setting is a command-line option without the dashes: `--ratio 0.22` → `ratio 0.22`.
- A flag on the command line overrides the file for that run:
  `./watchalong.sh --job jobs/my-show.txt --range 0:15:00-0:18:00`.
- `jobs/` is ignored by git, so your job files stay yours.

**Many streams at once:** put several `stream` blocks in one file, or run a whole folder with
`--job jobs/`. Check everything first with `--dry-run` (it downloads nothing).

- Jobs run one after another. If one fails, the rest still run, and a summary at the end
  shows what finished, what was skipped and what failed and why.
- Jobs whose output already exists are skipped, so after fixing a problem just run the same
  command again. `--force` redoes them.
- Each job's log is in `data/logs/<name>.log`.
- Each finished job leaves about 10 GB of working files unless you set `cleanup`: `render`
  frees most of it, `downloads` also deletes the downloaded stream and episodes, `all`
  keeps only the output video.

Local files can be anywhere: your home folder, an external drive, `/mnt/media`. Use
normal paths, absolute, relative or with `~`. With Docker, `watchalong.sh` shares just the
folders your command and job files point to, read-only, and prints each one it shares.

## 3. Video sources

The stream and the episodes can each be:

| Source | Example | What happens |
|---|---|---|
| YouTube link | `https://www.youtube.com/live/eFYByyw5AJc` | Downloaded with yt-dlp. For episodes, a playlist link counts as all of its videos, in playlist order. |
| nyaa.si page, `.torrent` link or magnet | `https://nyaa.si/view/1325663` | Downloaded as a torrent. A batch torrent counts as all of its episodes (a stream torrent must hold one video). |
| Local video file | `~/vods/stream.mp4` | Used in place. |
| Local folder (episodes only) | `~/anime/Some Show/` | Every video in it, in filename order. |

Episodes need their subtitles **in the video**: burned into the picture (as on YouTube), or
as a subtitle track (as in most fansub and simulcast `.mkv` releases, which are burned in
for you). A subtitle file next to a local episode with the same name (`.ass`, `.srt`,
`.vtt`) works too.

Many torrent releases start with a few seconds of distributor logo that the stream didn't
show. That's detected and cut automatically when you give two or more episodes of the same
release; otherwise end the episode line with `skip=SECONDS`.

## 4. Subtitles for the streamer

| `subs` | What you get |
|---|---|
| `english` (default) | Her lines translated to English. The translator also sees the anime's dialogue from the same moment, so names and reactions make sense. Needs Claude Code. |
| `japanese` | Her lines as a Japanese transcript. |
| `none` | No subtitles for her (also the fastest: speech recognition is skipped). |

**Names:** speech recognition spells names it doesn't know phonetically. With `subs english`
the translator gets a vocabulary list to fix them: your `vocab` lines (her name, her fans'
name, catchphrases) plus the anime's characters and places, looked up automatically
(`no-auto-vocab` turns that off).

## 5. Models

| Setting | Default | Choices |
|---|---|---|
| `asr-model` | `large-v3-turbo` | Speech recognition (faster-whisper): `large-v3` is slower and slightly better; `medium`, `small` are faster and worse. |
| `translator` | `claude-code` | Who translates `english` subs. |
| `translate-model` | Claude Code's default | `sonnet`, `opus`, `haiku`, or a full model name. |

Changing a model only redoes the step that uses it; everything else is reused.

## All settings

Every setting works on the command line (`--name value`) and in job files (`name value`).

| Setting | Default | |
|---|---|---|
| `subs` | `english` | `none`, `japanese` or `english`, see [Subtitles](#4-subtitles-for-the-streamer). |
| `asr-model`, `translator`, `translate-model` | | See [Models](#5-models). |
| `vocab` | | Names and words for the translator, `Japanese=English, ...`. |
| `no-auto-vocab` | off | Don't look up the anime's names automatically. |
| `ratio` | `0.25` | Width of the streamer strip, as a share of the video. Try 0.2–0.3. |
| `crop-offset` | `0` | Move the strip's crop right (+) or left (−), in pixels, if she isn't centered. |
| `anime-volume`, `voice-volume` | `0.8`, `1.0` | Mix levels. |
| `no-duck` | off | Don't lower the anime's volume while she talks. |
| `anime-skip` | `auto` | Seconds of distributor logo before each episode, or `auto`. |
| `timer-roi` | auto | Where the timer is (`x,y,width,height` in pixels), if it isn't found. |
| `range` | whole stream | Only make this part, e.g. `0:15:00-0:18:00`. |
| `no-render` | off | Stop once her subtitles are written (`data/work/<id>/vtuber.ass`). |
| `preset`, `crf` | `veryfast`, `20` | Encoding speed and quality (lower crf = better, bigger). |
| `name` | the video's id | Output file name, without `.mp4`. |
| `cleanup` | `none` | After a job, delete `render` pieces, `downloads` too, or `all` working files. |
| `cookies` | | YouTube cookies file, for members-only streams. |
| `render-jobs` | cores / 3 | How many video pieces render at once. |
| `workers` | all cores | Parallel timer readers. |
| `--job` | | A job file, or a folder of them. Command line only. |
| `--dry-run` | | Check everything and stop. Command line only. |
| `--force` | | Redo jobs whose output exists. |

## Checking before a long render

```sh
# a few minutes of video, to check sync and layout (fast, no subtitles)
./watchalong.sh --job jobs/my-show.txt --range 0:15:00-0:18:00 --subs none

# subtitles only, to read them before rendering anything
./watchalong.sh --job jobs/my-show.txt --no-render
```

Downloads, sync, transcripts and translations are all saved, so the full run afterwards
only does the remaining work.

## How long it takes

On a recent 10-core laptop, for a 3-hour stream (after downloads):

| Step | Time |
|---|---|
| Finding and reading the timer | 5–15 min |
| Speech recognition | 20–40 min |
| Translation | 2–3 min |
| Rendering | about 45 min |

Keep the computer awake: on macOS sleep pauses everything. Run it as
`caffeinate -s ./watchalong.sh ...`. If it's interrupted anyway, run the same command again;
it picks up where it stopped.

## Troubleshooting

- **"couldn't find an on-screen timer"**: find the timer's position in a screenshot of the
  stream and set `timer-roi x,y,width,height`.
- **"sync failed" / the number of episodes doesn't match**: the run stops instead of making a
  wrong video. Check that the episodes are listed in the order she watched them and that none
  is missing or extra. The message shows what it found in the stream.
- **She reacts a few seconds before things happen**: the episode file starts with a logo the
  stream didn't have and it wasn't detected (e.g. only one episode). Add `skip=SECONDS` to the
  episode line.
- **The strip cuts off her model**: adjust `crop-offset` and `ratio`, checking with a short
  `range` preview.
- **A torrent has no seeders**: swap the link for another release and re-run; only that
  episode is downloaded again.
- **Members-only stream**: export your YouTube cookies to `data/cookies.txt` and set
  `cookies /data/cookies.txt` (Docker) or `cookies data/cookies.txt` (on your machine).
- **YouTube downloads suddenly fail**: update yt-dlp: `docker compose build --no-cache`
  (Docker) or `pip install -U yt-dlp` (on your machine).
- **English translation fails in Docker**: the token in `.env` is missing or expired; run
  `claude setup-token` again.

## Personal use only

The output contains the anime itself. Watch it yourself; don't upload it.
