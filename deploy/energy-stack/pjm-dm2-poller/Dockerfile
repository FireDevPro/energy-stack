FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --system --no-create-home --uid 1010 poller && chown -R poller /app
USER poller

ENV PYTHONUNBUFFERED=1

# Polls hourly; fetchers fire only on their scheduled hours. The marker
# is touched on cycles where EITHER no feed was due (legitimate idle wake)
# OR at least one due feed succeeded. A cycle where every due feed fails
# leaves the marker untouched, so persistent auth/schema/4xx/5xx failures
# eventually flip the container unhealthy instead of hiding behind "loop
# is alive". Per-feed observability lives in the `pjm.feed_status` Influx
# measurement (one point per feed attempt). 90-min staleness budget is
# generous because the rarest scheduled feeds (annual_zonal_nspl on Dec 1
# at 03:00) have a long natural quiet period — but every other due hour
# in a healthy week touches the marker, so 90 min is the right floor.
HEALTHCHECK --interval=600s --timeout=5s --start-period=120s --retries=2 \
  CMD test -n "$(find /tmp/last_poll_ok -mmin -90 2>/dev/null)" || exit 1

CMD ["python", "app.py"]
