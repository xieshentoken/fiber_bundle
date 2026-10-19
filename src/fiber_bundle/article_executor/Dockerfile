FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/fiber
WORKDIR /opt/fiber/fiber_bundle
COPY __init__.py articles.py article_dates.py article_fetch.py article_capture.py article_worker_entry.py config.py ./
COPY vendor/__init__.py vendor/article_parser.py vendor/article-source.json vendor/wechat_image_message.py vendor/wechat-to-obsidian-LICENSE.txt ./vendor/
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "fiber_bundle.article_worker_entry"]
