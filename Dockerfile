FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286
WORKDIR /bot
COPY bot.py arena.py guard.py lane.py mechfight.py /bot/
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
