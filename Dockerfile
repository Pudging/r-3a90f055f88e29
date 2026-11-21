FROM python:3.12-slim
WORKDIR /bot
COPY bot.py shelter.py cover.bin mechfight.py ./
USER 65534:65534
ENTRYPOINT ["python3", "-B", "-u", "/bot/bot.py"]
