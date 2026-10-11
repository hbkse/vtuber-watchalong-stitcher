FROM python:3.12-slim-trixie

# ffmpeg (Debian's build includes libass for burning subtitles), aria2 for torrents,
# tesseract for reading the on-screen timer, Noto CJK for Japanese subtitles.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg aria2 tesseract-ocr fonts-noto-cjk ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
COPY --from=denoland/deno:bin-2.1.4 /deno /usr/local/bin/deno

# Claude Code CLI, for --translator claude-code (auth: CLAUDE_CODE_OAUTH_TOKEN in .env)
RUN curl -fsSL https://claude.ai/install.sh | bash && ln -s /root/.local/bin/claude /usr/local/bin/claude
ENV DISABLE_AUTOUPDATER=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY watchalong ./watchalong
RUN pip install --no-cache-dir .

ENV WATCHALONG_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
ENTRYPOINT ["watchalong"]
