# Open-Dynamo in one container: the web app and the compute server.
#   docker build -t open-dynamo .
#   docker run -p 8000:8000 open-dynamo

FROM node:22-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build                       # writes /build/src/opendynamo/static

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENDYNAMO_CACHE=/home/dynamo/.cache/opendynamo
RUN useradd --create-home dynamo
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY --from=web /build/src/opendynamo/static ./src/opendynamo/static
RUN pip install -e . && chown -R dynamo /app
USER dynamo
# Compile one model now: this builds the compiler's support libraries into the image (a one-off of
# about half a minute), so the first student does not wait for it, and proves the image works.
RUN python -c "from opendynamo import Model; from opendynamo.examples import examples; m = Model(examples()[0]); m.simulate(); m.compiled.wasm()"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info')"
CMD ["opendynamo", "serve", "--host", "0.0.0.0", "--port", "8000"]
