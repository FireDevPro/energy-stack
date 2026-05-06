FROM influxdb:2.7

# influxdb:2.7 already includes the `influx` CLI; no extra packages needed.
# The container runs once per `compose up`, applies state idempotently, and exits.

WORKDIR /app
COPY apply.sh /app/apply.sh
COPY tasks/ /app/tasks/

RUN chmod +x /app/apply.sh

ENTRYPOINT ["/app/apply.sh"]
