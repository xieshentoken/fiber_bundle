FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
RUN apt-get update && apt-get install --no-install-recommends -y ffmpeg=7:5.1.9-0+deb12u1 \
    && mkdir /opt/runtime \
    && dpkg-query -W > /opt/runtime/packages.tsv \
    && sha256sum /usr/bin/ffmpeg /usr/bin/ffprobe > /opt/runtime/tools.sha256 \
    && ffmpeg -version > /opt/runtime/ffmpeg.txt \
    && ffprobe -version > /opt/runtime/ffprobe.txt \
    && cp /etc/apt/sources.list.d/debian.sources /opt/runtime/debian.sources \
    && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/fiber
COPY fiber_bundle /opt/fiber/fiber_bundle
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "fiber_bundle.media_worker_entry"]
