FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends chromium xvfb xauth openssl libnss3-tools && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/poetry && /opt/poetry/bin/pip install --no-cache-dir poetry==2.5.1
RUN python -m venv /opt/app
ENV VIRTUAL_ENV=/opt/app
ENV PATH="/opt/app/bin:/opt/poetry/bin:$PATH"
ENV POETRY_NO_INTERACTION=1
ENV POETRY_REQUESTS_TIMEOUT=120
ENV POETRY_INSTALLER_MAX_WORKERS=4
COPY pyproject.toml poetry.lock poetry.toml README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/pypoetry poetry sync --with dev --no-root
COPY src ./src
COPY tests ./tests
COPY docker ./docker
RUN poetry build --format wheel && python -m pip install --no-deps dist/*.whl
RUN useradd --create-home --uid 1000 tester
USER tester
ENV HOME=/home/tester
CMD ["sh", "-c", "python -m pytest -q -p no:cacheprovider tests && xvfb-run -a python docker/test_login.py"]
