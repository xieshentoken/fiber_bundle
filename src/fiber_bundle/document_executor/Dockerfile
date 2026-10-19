FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/fiber
COPY fiber_bundle /opt/fiber/fiber_bundle
COPY pypdf /opt/fiber/pypdf
COPY PYPDF-LICENSE context.json /opt/fiber/
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "fiber_bundle.document_worker_entry"]
