FROM debian:bookworm-slim
# calibre = ebook-convert / ebook-meta, rclone = one-way pull from the seedbox
RUN apt-get update \
 && apt-get install -y --no-install-recommends calibre rclone python3 ca-certificates fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
# unar = extracts the .rar/.zip author bundles in the archive (own layer so calibre stays cached)
RUN apt-get update \
 && apt-get install -y --no-install-recommends unar \
 && rm -rf /var/lib/apt/lists/*
ENV QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
COPY books.py /app/books.py
ENTRYPOINT ["python3", "/app/books.py"]
