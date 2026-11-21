FROM python:3.13-slim
WORKDIR /bot
COPY bot.py mechfight.py base_policy.py strategy.py nav.bin ./
USER 65534:65534
ENTRYPOINT ["python3", "-B", "-u", "/bot/bot.py"]
