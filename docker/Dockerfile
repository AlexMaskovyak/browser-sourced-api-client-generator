FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
WORKDIR /work
RUN apt-get update && apt-get install -y --no-install-recommends chromium xvfb xauth openssl libnss3-tools && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests
COPY docker ./docker
RUN python -m build --wheel --no-isolation && pip install --no-deps dist/*.whl
RUN useradd --create-home --uid 1000 tester
USER tester
ENV HOME=/home/tester
CMD ["sh", "-c", "python -m pytest -q -p no:cacheprovider tests && xvfb-run -a python docker/test_login.py"]
