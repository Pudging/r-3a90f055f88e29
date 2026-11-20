# Build from this folder: docker build -t my-bot .
# The match runs it with no network, a read-only root (writable /tmp), 1 CPU, 512 MB, and the map at /arena.
FROM python:3.13-slim
WORKDIR /bot
COPY bot.py mechfight.py strategy.py ./
USER 65534:65534
ENTRYPOINT ["python3", "-B", "-u", "/bot/bot.py"]
