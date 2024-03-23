FROM python:3.11-slim
RUN apt update -y && \
    apt install -y chromium-driver && \
    apt clean && \
    pip install poetry

WORKDIR /maltelogin

COPY app.py .
COPY ptc_auth.py .
COPY poetry.lock .
COPY pyproject.toml .

RUN poetry install

ENTRYPOINT ["poetry", "run", "litestar", "run"]
