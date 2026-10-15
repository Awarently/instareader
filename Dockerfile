FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY instareader ./instareader

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app

ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+(os.getenv('HTTP_PORT') or '8080')+'/health',timeout=4)" || exit 1

CMD ["python", "-m", "instareader"]
