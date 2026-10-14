# Hatchtower als Container (Alternative zum LXC-/Debian-Installer)
#   docker compose up -d --build
# Daten (Datenbank, Logos, Schlüssel) liegen im Volume /var/lib/hatchtower.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HATCHTOWER_DATA_DIR=/var/lib/hatchtower \
    HATCHTOWER_PORT=8000 \
    HATCHTOWER_DOCKER=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home-dir /var/lib/hatchtower --shell /usr/sbin/nologin hatchtower

COPY app/requirements.txt /opt/hatchtower/app/requirements.txt
RUN pip install --no-cache-dir -r /opt/hatchtower/app/requirements.txt

COPY app /opt/hatchtower/app
COPY VERSION LICENSE /opt/hatchtower/
COPY tools/third_party_licenses.py /tmp/third_party_licenses.py
RUN python /tmp/third_party_licenses.py > /opt/hatchtower/THIRD_PARTY_LICENSES.txt || true; rm -f /tmp/third_party_licenses.py
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/hatchtower-entrypoint
COPY --chmod=755 docker/hatchtower /usr/local/bin/hatchtower

RUN mkdir -p /var/lib/hatchtower && chown hatchtower:hatchtower /var/lib/hatchtower
USER hatchtower
WORKDIR /opt/hatchtower/app
VOLUME /var/lib/hatchtower
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/login' % os.environ.get('HATCHTOWER_PORT','8000'), timeout=4)" || exit 1

ENTRYPOINT ["hatchtower-entrypoint"]
