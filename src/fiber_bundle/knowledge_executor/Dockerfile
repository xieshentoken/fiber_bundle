FROM python:3.13.12-slim-bookworm@sha256:a58daefb915e1e03ad48f3ca4df8832065412c5c35cacb9d39f4229184de12b6
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/deps:/opt/fiber HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
COPY deps /opt/deps
COPY fiber_bundle /opt/fiber/fiber_bundle
COPY model /opt/graph/model
COPY context.json /opt/graph/context.json
COPY SEMANTICA-LICENSE /opt/graph/SEMANTICA-LICENSE
USER 1000:1000
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "fiber_bundle.knowledge_graph.worker_entry"]
