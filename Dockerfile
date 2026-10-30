# Pinned, with the package versions in constraints.txt: a new release cannot change the demo under a judge.
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /app

COPY pyproject.toml README.md constraints.txt ./
COPY nazar_agent ./nazar_agent
RUN pip install --no-cache-dir -c constraints.txt . \
    && useradd --system --uid 10001 agent \
    && mkdir /data && chown agent /data

USER agent
# Proposals, the audit trail and the demo usage live on a volume.
ENV AGENT_DB_PATH=/data/nazar_agent.sqlite3 SERVICE_HOST=0.0.0.0 SERVICE_PORT=8100
# Listening on the network: only people who opened it from NAZAR, over HTTPS.
ENV REQUIRE_SIGN_IN=true COOKIE_SECURE=true
EXPOSE 8100
CMD ["python", "-m", "nazar_agent.service"]
